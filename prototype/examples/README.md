# Example Sky apps

1. [**`resnet_app.py`**](./resnet_app.py): ResNet50 training on GPUs, adapted from [tensorflow/tpu](https://github.com/tensorflow/tpu).

    Do the following before running the app:
    ```bash
    # To run on GPUs, we prepared a fork that added a few changes.
    git clone -b gpu_train https://github.com/concretevitamin/tpu ~/Downloads/tpu
    pushd ~/Downloads/tpu
    git checkout 222cc86b5
    # See diff against the last common commit with upstream: git diff adecd6
    popd
    ```
    The training data is currently a public, "fake_imagenet" dataset (`gs://cloud-tpu-test-datasets/fake_imagenet`, 70GB).

## TODO: non-runnable apps
1. [**`timm_app.py`**](./timm_app.py): the [PyTorch image models (timm)](https://github.com/rwightman/pytorch-image-models) package.
  - Not runnable due to ImageNet data must be on a local disk, and we don't have data movement yet.