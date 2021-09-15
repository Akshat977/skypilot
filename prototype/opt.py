"""Assumptions:

- Users supply a estimate_runtime_func() for all nodes in the DAG.
- Assuming homogeneous instances:
  - Support for choosing # instances
- Support for heterogeneous instances?


Optimization problem:
 - min cost, subject to some constraints?
 - min latency, subject to some constraints?

DAG assumption: chain.  If multiple branches, take into account of parallelism?

Incorporate the notion of region/zone (affects pricing).
Incorporate the notion of per-account egress quota (affects pricing).
"""
import collections
import copy
import enum
import pprint
import typing

import networkx as nx
import numpy as np


class Cloud(object):

    # TODO: incorporate region/zone into the API.
    def instance_type_to_hourly_cost(self, instance_type):
        """Returns the hourly on-demand price for an instance type."""
        raise NotImplementedError

    def get_egress_cost(self, num_gigabytes):
        """Returns the egress cost.

        TODO: takes into account "per month" accumulation per account.
        """
        raise NotImplementedError

    def is_same_cloud(self, other):
        raise NotImplementedError


class AWS(Cloud):
    _REPR = 'AWS'

    # In general, query this from the cloud.
    _ON_DEMAND_PRICES = {
        # p3.
        'p3.2xlarge': 3.06,
        'p3.8xlarge': 12.24,
        'p3.16xlarge': 24.48,

        # inf1.
        'inf1.xlarge': 0.228,
        'inf1.2xlarge': 0.362,
        'inf1.6xlarge': 1.18,
        'inf1.24xlarge': 4.721,
    }

    def instance_type_to_hourly_cost(self, instance_type):
        return AWS._ON_DEMAND_PRICES[instance_type]

    def get_egress_cost(self, num_gigabytes):
        # In general, query this from the cloud:
        #   https://aws.amazon.com/s3/pricing/
        # NOTE: egress from US East (Ohio).
        if num_gigabytes > 150 * 1024:
            return 0.05 * num_gigabytes
        cost = 0.0
        if num_gigabytes >= 50 * 1024:
            cost += (num_gigabytes - 50 * 1024) * 0.07
            num_gigabytes -= 50 * 1024

        if num_gigabytes >= 10 * 1024:
            cost += (num_gigabytes - 10 * 1024) * 0.085
            num_gigabytes -= 10 * 1024

        if num_gigabytes > 1:
            cost += (num_gigabytes - 1) * 0.09

        cost += 0.0
        return cost

    def __repr__(self):
        return AWS._REPR

    def is_same_cloud(self, other):
        return isinstance(other, AWS)


class GCP(Cloud):

    _REPR = 'GCP'

    # In general, query this from the cloud.
    # NOTE: assumes us-central1.
    _ON_DEMAND_PRICES = {
        # GPUs: https://cloud.google.com/compute/gpus-pricing.
        # V100
        '1x V100': 2.48,
        '2x V100': 2.48 * 2,
        '4x V100': 2.48 * 4,
        '8x V100': 2.48 * 8,
        # T4
        '1x T4': 0.35,
        '2x T4': 0.35 * 2,
        '4x T4': 0.35 * 4,
        # TPUs: https://cloud.google.com/tpu/pricing.
        'tpu-v2-8': 4.5,
        'tpu-v3-8': 8.0,
        # VMs: https://cloud.google.com/compute/all-pricing.
        'n1-standard-4': 0.189999,
        'n1-standard-8': 0.379998,
    }

    def instance_type_to_hourly_cost(self, instance_type):
        return GCP._ON_DEMAND_PRICES[instance_type]

    def get_egress_cost(self, num_gigabytes):
        # In general, query this from the cloud:
        #   https://cloud.google.com/storage/pricing#network-pricing
        # NOTE: egress to worldwide (excl. China, Australia).
        if num_gigabytes <= 1024:
            return 0.12 * num_gigabytes
        elif num_gigabytes <= 1024 * 10:
            return 0.11 * num_gigabytes
        else:
            return 0.08 * num_gigabytes

    def __repr__(self):
        return GCP._REPR

    def is_same_cloud(self, other):
        return isinstance(other, GCP)


class Azure(Cloud):
    pass


class Hardware(typing.NamedTuple):
    cloud: Cloud
    types: typing.Tuple[str]

    def __repr__(self) -> str:
        return f'{self.cloud}({self.types})'
        # return f'{self.cloud.name}({self.types})'

    def get_cost(self, seconds):
        """Returns cost in USD for the runtime in seconds."""

        cost = 0.0
        typs = self.types
        if type(typs) is str:
            typs = [typs]
        for instance_type in typs:
            hourly_cost = self.cloud.instance_type_to_hourly_cost(instance_type)
            cost += hourly_cost * (seconds / 3600)
        return cost


class DummyHardware(Hardware):
    """A dummy Hardware that has zero egress cost from/to."""

    _REPR = 'DummyCloud'

    def __repr__(self) -> str:
        return DummyHardware._REPR

    def get_cost(self, seconds):
        return 0


class DummyCloud(Cloud):
    """A dummy Cloud that has zero egress cost from/to."""
    pass


# DAGs.
class DagContext(object):
    _current_dag = None
    _previous_dags = []

    @classmethod
    def push_dag(cls, dag):
        if cls._current_dag:
            cls._previous_dags.append(cls._current_dag)
        cls._current_dag = dag

    @classmethod
    def pop_dag(cls):
        old_dag = cls._current_dag
        if cls._previous_dags:
            cls._current_dag = cls._previous_dags.pop()
        else:
            cls._current_dag = None
        return old_dag

    @classmethod
    def get_current_dag(cls):
        return cls._current_dag


class Dag(object):
    """FIXME: assume a chain DAG for now."""

    _PREVIOUS_DAGS = []
    _CURRENT_DAG = None

    def __init__(self):
        self.operators = []
        self.graph = nx.DiGraph()

    def add(self, operator):
        self.graph.add_node(operator)
        self.operators.append(operator)

    def add_edge(self, op1, op2):
        assert op1 in self.graph.nodes
        assert op2 in self.graph.nodes
        self.graph.add_edge(op1, op2)

    def __enter__(self):
        DagContext.push_dag(self)
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        DagContext.pop_dag()

    def __repr__(self):
        pformat = pprint.pformat(self.operators)
        return 'DAG:\n{}'.format(pformat)
        # return '<DAG=[{}]>'.format(','.join(map(str, self.operators)))

    def get_graph(self):
        return self.graph


class PlannedDag(object):
    pass


# Optimizer.
class SkyOptimizer(object):

    # Constants: minimize what target?
    COST = 0
    TIME = 1

    @staticmethod
    def _egress_cost(src_cloud, dst_cloud, gigabytes):
        if isinstance(src_cloud, DummyCloud) or isinstance(
                dst_cloud, DummyCloud):
            return 0.0

        if not src_cloud.is_same_cloud(dst_cloud):
            egress_cost = src_cloud.get_egress_cost(num_gigabytes=gigabytes)
        else:
            egress_cost = 0.0
        if egress_cost > 0:
            print('  {} -> {} egress cost: ${} for {:.1f} GB'.format(
                src_cloud, dst_cloud, egress_cost, gigabytes))
        return egress_cost

    @staticmethod
    def _egress_time(src_cloud, dst_cloud, gigabytes):
        """Returns estimated egress time in seconds."""
        # FIXME: estimate bandwidth between each cloud-region pair.
        if isinstance(src_cloud, DummyCloud) or isinstance(
                dst_cloud, DummyCloud):
            return 0.0
        if not src_cloud.is_same_cloud(dst_cloud):
            # 10Gbps is close to the average of observed b/w from S3
            # (us-west,east-2) to GCS us-central1, assuming highly sharded files
            # (~128MB per file).
            bandwidth_gbps = 10
            egress_time = gigabytes * 8 / bandwidth_gbps
            print('  {} -> {} egress time: {} s for {:.1f} GB'.format(
                src_cloud, dst_cloud, egress_time, gigabytes))
        else:
            egress_time = 0.0
        return egress_time

    @staticmethod
    def optimize(dag: Dag, minimize) -> PlannedDag:
        dag = SkyOptimizer._add_dummy_source_sink_nodes(copy.deepcopy(dag))
        return SkyOptimizer._optimize_cost(
            dag, minimize_cost=minimize == SkyOptimizer.COST)

    @staticmethod
    def _add_dummy_source_sink_nodes(dag: Dag):
        """Adds special Source and Sink nodes.

        The two special nodes are for conveniently handling cases such as

          { A, B } --> C (multiple sources)

        or

          A --> { B, C } (multiple sinks)

        Adds new edges:
            Source --> for all node with in degree 0
            For all node with out degree 0 --> Sink
        """
        graph = dag.get_graph()
        zero_indegree_nodes = []
        zero_outdegree_nodes = []
        for node, in_degree in graph.in_degree():
            if in_degree == 0:
                zero_indegree_nodes.append(node)
        for node, out_degree in graph.out_degree():
            if out_degree == 0:
                zero_outdegree_nodes.append(node)

        def make_dummy(name):
            dummy = Operator(name)
            dummy.set_allowed_hardware({DummyHardware(DummyCloud(), None)})
            dummy.set_estimate_runtime_func(lambda _: 0)
            return dummy

        with dag:
            source = make_dummy('__source__')
            for real_source_node in zero_indegree_nodes:
                source >> real_source_node
            sink = make_dummy('__sink__')
            for real_sink_node in zero_outdegree_nodes:
                real_sink_node >> sink
        return dag

    @staticmethod
    def _optimize_cost(dag: Dag, minimize_cost=True):
        graph = dag.get_graph()
        topo_order = list(nx.topological_sort(graph))

        # FIXME: write to (node, cloud) as egress cost depends only on clouds.
        # node -> {hardware -> best estimated cost}
        dp_best_cost = collections.defaultdict(dict)
        # node -> {hardware -> (parent, best parent hardware)}
        dp_point_backs = collections.defaultdict(dict)

        for node_i, node in enumerate(topo_order):
            # Base case: a special source node.
            if node_i == 0:
                dp_best_cost[node][list(node.get_allowed_hardware())[0]] = 0
                continue
            # Don't print for the last node, Sink.
            do_print = node_i != len(topo_order) - 1

            if do_print:
                print('\n#### {} ####'.format(node))
            parents = list(graph.predecessors(node))

            assert len(parents) == 1, 'Supports single parent for now'
            parent = parents[0]

            for hardware in node.get_allowed_hardware():
                # Computes dp_best_cost[node][hardware]
                #   = my estimated cost + min { pred_cost + egress_cost }
                assert hardware not in dp_best_cost[node]
                if do_print:
                    print('hardware:', hardware)

                estimated_runtime = node.estimate_runtime(hardware)
                if minimize_cost:
                    estimated_cost = hardware.get_cost(estimated_runtime)
                else:
                    # Minimize run time; overload the term 'cost'.
                    estimated_cost = estimated_runtime
                min_pred_cost_plus_egress = np.inf
                if do_print:
                    print('  estimated_runtime: {:.0f} s ({:.1f} hr)'.format(
                        estimated_runtime, estimated_runtime / 3600))
                    if minimize_cost:
                        print('  estimated_cost (no egress): ${:.1f}'.format(
                            estimated_cost))

                def _egress(parent, parent_hardware, node, hardware):
                    if isinstance(parent_hardware.cloud, DummyCloud):
                        # Special case.  The current 'node' is a real
                        # source node, and its input may be on a different
                        # cloud from 'hardware'.
                        src_cloud = node.get_inputs_cloud()
                        nbytes = node.get_estimated_inputs_size_gigabytes()
                    else:
                        src_cloud = parent_hardware.cloud
                        nbytes = parent.get_estimated_outputs_size_gigabytes()
                    dst_cloud = hardware.cloud

                    if minimize_cost:
                        fn = SkyOptimizer._egress_cost
                    else:
                        fn = SkyOptimizer._egress_time
                    return fn(src_cloud, dst_cloud, nbytes)

                for parent_hardware, parent_cost in dp_best_cost[parent].items(
                ):
                    egress_cost = _egress(parent, parent_hardware, node,
                                          hardware)
                    if parent_cost + egress_cost < min_pred_cost_plus_egress:
                        min_pred_cost_plus_egress = parent_cost + egress_cost
                        best_parent = parent_hardware
                        best_egress_cost = egress_cost
                if do_print:
                    print('  best_parent', best_parent)
                dp_point_backs[node][hardware] = (parent, best_parent,
                                                  best_egress_cost)
                dp_best_cost[node][
                    hardware] = min_pred_cost_plus_egress + estimated_cost

        print('\nOptimizer - dp_best_cost:')
        pprint.pprint(dict(dp_best_cost))

        return SkyOptimizer.print_optimized_plan(dp_best_cost, topo_order,
                                                 dp_point_backs, minimize_cost)

    @staticmethod
    def print_optimized_plan(dp_best_cost, topo_order, dp_point_backs,
                             minimize_cost):
        # FIXME: this function assumes chain.
        node = topo_order[-1]
        messages = []
        egress_cost = 0.0
        overall_best = None
        while True:
            best_costs = dp_best_cost[node]
            h, c = None, np.inf
            for hardware in best_costs:
                if best_costs[hardware] < c:
                    h = hardware
                    c = best_costs[hardware]
            # TODO: avoid modifying in-place.
            node.best_hardware = h
            if not isinstance(h, DummyHardware):
                messages.append('  {} : {}'.format(node, h))
            elif overall_best is None:
                overall_best = c
            if node not in dp_point_backs:
                break
            egress_cost = dp_point_backs[node][h][2]
            node = dp_point_backs[node][h][0]
        if minimize_cost:
            print('\nOptimizer - plan minimizing cost (~${:.1f}):'.format(
                overall_best))
        else:
            print('\nOptimizer - plan minimizing run time (~{:.1f} hr):'.format(
                overall_best / 3600))
        for msg in reversed(messages):
            print(msg)


# Operator.
class Operator(object):
    """Operator: a coarse-grained stage in an application."""

    def __init__(self, name=None, command=None, args=None):
        self.name = name
        # The script and args to run.
        self.command = command
        self.args = args

        self.inputs = None
        self.outputs = None
        self.estimated_inputs_size_gigabytes = None
        self.estimated_outputs_size_gigabytes = None
        self.allowed_hardware = None
        self.estimate_runtime_func = None

        dag = DagContext.get_current_dag()
        dag.add(self)

    # E.g., 's3://bucket', 'gcs://bucket', or None.
    def set_inputs(self, inputs, estimated_size_gigabytes):
        self.inputs = inputs
        self.estimated_inputs_size_gigabytes = estimated_size_gigabytes

    def get_inputs(self):
        return self.inputs

    def get_estimated_inputs_size_gigabytes(self):
        return self.estimated_inputs_size_gigabytes

    def get_inputs_cloud(self):
        """Returns the cloud my inputs live in."""
        assert type(self.inputs) is str, self.inputs
        if self.inputs.startswith('s3:'):
            return AWS()
        elif self.inputs.startswith('gcs:'):
            return GCP()
        else:
            assert False, 'cloud path not supported: {}'.format(self.inputs)

    def set_outputs(self, outputs, estimated_size_gigabytes):
        self.outputs = outputs
        self.estimated_outputs_size_gigabytes = estimated_size_gigabytes

    def get_outputs(self):
        return self.outputs

    def get_estimated_outputs_size_gigabytes(self):
        return self.estimated_outputs_size_gigabytes

    def set_allowed_hardware(self, allowed_hardware):
        """Sets the allowed cloud-instance type combos to execute this op."""
        self.allowed_hardware = allowed_hardware

    def get_allowed_hardware(self):
        return self.allowed_hardware

    def set_estimate_runtime_func(self, func):
        """Sets a func mapping hardware to estimated time (secs)."""
        self.estimate_runtime_func = func

    def estimate_runtime(self, hardware):
        """Returns a func mapping hardware to estimated time (secs)."""
        if self.estimate_runtime_func is None:
            raise NotImplementedError(
                'Node [{}] does not have a cost model set; '
                'call set_estimate_runtime_func() first'.format(self))
        return self.estimate_runtime_func(hardware)

    def __rshift__(a, b):
        DagContext.get_current_dag().add_edge(a, b)

    def __repr__(self):
        if self.name:
            return self.name
        s = 'Operator(cmd={}, args={})'.format(self.command, self.args)
        s += '\n  inputs: {}'.format(self.inputs)
        s += '\n  outputs: {}'.format(self.outputs)
        s += '\n  allowed_hardware: {}'.format(self.allowed_hardware)
        return s


def resnet50_estimate_runtime(hardware):
    """A simple runtime model for Resnet50."""
    # 3.8 G Multiply-Adds, 2 FLOPs per MADD, 3 for fwd+bwd.
    flops_for_one_image = 3.8 * (10**9) * 2 * 3

    if isinstance(hardware.cloud, AWS):
        instance = hardware.types
        if instance == 'p3.2xlarge':
            num_v100s = 1
        elif instance == 'p3.8xlarge':
            num_v100s = 4
        elif instance == 'p3.16xlarge':
            num_v100s = 8
        else:
            assert False, 'Not supported: {}'.format(hardware)

        # Adds communication overheads per step (in seconds).
        communication_slack = 0.0
        if num_v100s == 4:
            communication_slack = 0.15
        elif num_v100s == 8:
            communication_slack = 0.30

        max_per_device_batch_size = 256
        effective_batch_size = max_per_device_batch_size * num_v100s

        # 112590 steps, 1024 BS = 90 epochs.
        total_steps = 112590 * (1024.0 / effective_batch_size)
        flops_for_one_batch = flops_for_one_image * max_per_device_batch_size

        # 27 TFLOPs, harmonic mean b/t 15TFLOPs (single-precision) & 120 TFLOPs
        # (16 bit).
        utilized_flops = 27 * (10**12)
        print('****** trying 1/3 util for v100')
        utilized_flops = 120 * (10**12) / 3

        estimated_step_time_seconds = flops_for_one_batch / utilized_flops \
          + communication_slack
        estimated_run_time_seconds = estimated_step_time_seconds * total_steps

    elif isinstance(hardware.cloud, GCP):
        assert 'tpu-v3-8' in hardware.types, hardware
        tpu_v3_8_flops = 420 * (10**12)
        known_resnet50_utilization = 0.445  # From actual profiling.

        # GPU - fixed to 1/3 util
        # TPU
        #  - 1/4 util: doesn't work
        #  - 1/3 util: works
        #  - 1/2 util: works
        print('*** trying hand written util for TPU')
        known_resnet50_utilization = 1 / 3

        max_per_device_batch_size = 1024
        total_steps = 112590  # 112590 steps, 1024 BS = 90 epochs.
        flops_for_one_batch = flops_for_one_image * max_per_device_batch_size
        utilized_flops = tpu_v3_8_flops * known_resnet50_utilization
        estimated_step_time_seconds = flops_for_one_batch / utilized_flops
        estimated_run_time_seconds = estimated_step_time_seconds * total_steps

        print('  tpu-v3-8 estimated_step_time_seconds',
              estimated_step_time_seconds)

    else:
        assert False, 'not supported cloud in prototype: {}'.format(
            hardware.cloud)

    return estimated_run_time_seconds


def resnet50_infer_estimate_runtime(hardware):
    # 3.8 G Multiply-Adds, 2 FLOPs per MADD.
    flops_for_one_image = 3.8 * (10**9) * 2
    num_images = 0.1 * 1e6  # TODO: vary this.
    num_images = 1e6  # TODO: vary this.
    num_images = 70 * 1e6  # TODO: vary this.

    instance = hardware.types
    # assert instance in ['p3.2xlarge', 'inf1.2xlarge', 'nvidia-t4'], instance

    if instance == 'p3.2xlarge':
        # 120 TFLOPS TensorCore.
        print('****** trying 1/3 util for v100')
        utilized_flops = 120 * (10**12) / 3

        # # Max bs to keep p99 < 15ms.
        # max_per_device_batch_size = 8
        # max_per_device_batch_size = 8*1e3
        # max_per_device_batch_size = 1

        # num_v100s = 1
        # effective_batch_size = max_per_device_batch_size * num_v100s

        # # 112590 steps, 1024 BS = 90 epochs.
        # total_steps = num_images // effective_batch_size
        # flops_for_one_batch = flops_for_one_image * max_per_device_batch_size

        # estimated_step_time_seconds = flops_for_one_batch / utilized_flops
        # estimated_run_time_seconds = estimated_step_time_seconds * total_steps

        # TODO: this ignores offline vs. online.  It's a huge batch.
        estimated_run_time_seconds = \
            flops_for_one_image * num_images / utilized_flops
    elif instance == 'inf1.2xlarge':
        # Inferentia: 1 chip = 128T[F?]OPS
        # Each AWS Inferentia chip supports up to 128 TOPS (trillions of
        # operations per second) of performance [assume 16, as it casts to
        # bfloat16 by default).
        # TODO: also assume 1/3 utilization
        utilized_flops = 128 * (10**12) / 3
        # TODO: this ignores offline vs. online.  It's a huge batch.
        estimated_run_time_seconds = \
            flops_for_one_image * num_images / utilized_flops
    elif isinstance(instance, tuple) and instance[0] == '1x T4':
        # T4 GPU: 65 TFLOPS fp16
        utilized_flops = 65 * (10**12) / 3
        estimated_run_time_seconds = \
            flops_for_one_image * num_images / utilized_flops
    else:
        assert False, hardware

    # print('** num images {} total flops {}'.format(
    #     num_images, flops_for_one_image * num_images))

    return estimated_run_time_seconds


def make_application():
    """A simple application: train_op -> infer_op."""

    with Dag() as dag:
        # Train.
        train_op = Operator('train_op',
                            command='train.py',
                            args='--data_dir=INPUTS[0] --model_dir=OUTPUTS[0]')

        train_op.set_inputs(
            's3://my-imagenet-data',
            # estimated_size_gigabytes=150,
            # estimated_size_gigabytes=1500,
            estimated_size_gigabytes=600,
        )

        # 'CLOUD': saves to the cloud this op ends up executing on.
        train_op.set_outputs('CLOUD://my-model', estimated_size_gigabytes=0.1)

        train_op.set_allowed_hardware({
            Hardware(AWS(), 'p3.2xlarge'),  # 1 V100, EC2.
            Hardware(AWS(), 'p3.8xlarge'),  # 4 V100s, EC2.
            # Tuples mean all resources are required.
            Hardware(GCP(), ('n1-standard-8', 'tpu-v3-8')),
        })

        train_op.set_estimate_runtime_func(resnet50_estimate_runtime)

        # Infer.
        infer_op = Operator('infer_op',
                            command='infer.py',
                            args='--model_dir=INPUTS[0]')

        # Data dependency.
        # FIXME: make the system know this is from train_op's outputs.
        infer_op.set_inputs(train_op.get_outputs(),
                            estimated_size_gigabytes=0.1)

        infer_op.set_allowed_hardware({
            Hardware(AWS(), 'inf1.2xlarge'),
            Hardware(AWS(), 'p3.2xlarge'),
            Hardware(GCP(), ('1x T4', 'n1-standard-4')),
            Hardware(GCP(), ('1x T4', 'n1-standard-8')),
        })

        infer_op.set_estimate_runtime_func(resnet50_infer_estimate_runtime)

        # Chain the operators (Airflow syntax).
        # The dependency represents data flow.
        train_op >> infer_op

    return dag


dag = make_application()

SkyOptimizer.optimize(dag, minimize=SkyOptimizer.COST)

# SkyOptimizer.optimize(dag, minimize=SkyOptimizer.TIME)