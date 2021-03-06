# Copyright 2015 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""Builds the CIFAR-10 network.

Summary of available functions:

 # Compute input images and labels for training. If you would like to run
 # evaluations, use inputs() instead.
 inputs, labels = distorted_inputs()

 # Compute inference on the model inputs to make a prediction.
 predictions = inference(inputs)

 # Compute the total loss of the prediction with respect to the labels.
 loss = loss(predictions, labels)

 # Create a graph to run one step of training with respect to the loss.
 train_op = train(loss, global_step)
"""
# pylint: disable=missing-docstring
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import re
import sys
import tarfile

import numpy as np

from six.moves import urllib
import tensorflow as tf

import cifar10_input

FLAGS = tf.app.flags.FLAGS

# Basic model parameters.
tf.app.flags.DEFINE_integer('batch_size', 128,
                            """Number of images to process in a batch.""")
tf.app.flags.DEFINE_string('data_dir', './cifar10_data',
                           """Path to the CIFAR-10 data directory.""")
tf.app.flags.DEFINE_boolean('use_fp16', False,
                            """Train the model using fp16.""")
tf.app.flags.DEFINE_float('bn_momentum', 0.99,
                          """Momentum for the moving average of batch normalization.""")
tf.app.flags.DEFINE_float('wd', 0.004,
                          """L2Loss weight decay multiplied by this float.""")
tf.app.flags.DEFINE_integer('depth', 40,
                            """Depth of dense-net.""")
tf.app.flags.DEFINE_integer('growth_rate', 12,
                            """Growth rate of dense-net. This number of channels is added in each layer.""")
tf.app.flags.DEFINE_float('dropout_rate', 0.2,
                          """Drop out rate after convolution.""")
tf.app.flags.DEFINE_string('lr_boundaries', "150,225",
                           """Boundaries of learning rate.""")
tf.app.flags.DEFINE_string('lr_values', "0.1,0.01,0.001",
                           """Values of learning rate.""")

# Global constants describing the CIFAR-10 data set.
IMAGE_SIZE = cifar10_input.IMAGE_SIZE
NUM_CLASSES = cifar10_input.NUM_CLASSES
NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN = cifar10_input.NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN
NUM_EXAMPLES_PER_EPOCH_FOR_EVAL = cifar10_input.NUM_EXAMPLES_PER_EPOCH_FOR_EVAL


# Constants describing the training process.
MOVING_AVERAGE_DECAY = 0.9999     # The decay to use for the moving average.
NUM_EPOCHS_PER_DECAY = 100.0      # Epochs after which learning rate decays.
LEARNING_RATE_DECAY_FACTOR = 0.1  # Learning rate decay factor.
INITIAL_LEARNING_RATE = 1.0       # Initial learning rate.

# If a model is trained with multiple GPUs, prefix all Op names with tower_name
# to differentiate the operations. Note that this prefix is removed from the
# names of the summaries when visualizing a model.
TOWER_NAME = 'tower'

DATA_URL = 'http://www.cs.toronto.edu/~kriz/cifar-10-binary.tar.gz'


def _activation_summary(x):
    """Helper to create summaries for activations.

    Creates a summary that provides a histogram of activations.
    Creates a summary that measures the sparsity of activations.

    Args:
      x: Tensor
    Returns:
      nothing
    """
    # Remove 'tower_[0-9]/' from the name in case this is a multi-GPU training
    # session. This helps the clarity of presentation on tensorboard.
    tensor_name = re.sub('%s_[0-9]*/' % TOWER_NAME, '', x.op.name)
    tf.summary.histogram(tensor_name + '/activations', x)
    tf.summary.scalar(tensor_name + '/sparsity',
                      tf.nn.zero_fraction(x))


def _get_dtype():
    dtype = tf.float16 if FLAGS.use_fp16 else tf.float32
    return dtype


def distorted_inputs():
    """Construct distorted input for CIFAR training using the Reader ops.

    Returns:
      images: Images. 4D tensor of [batch_size, IMAGE_SIZE, IMAGE_SIZE, 3] size.
      labels: Labels. 1D tensor of [batch_size] size.

    Raises:
      ValueError: If no data_dir
    """
    if not FLAGS.data_dir:
        raise ValueError('Please supply a data_dir')
    data_dir = os.path.join(FLAGS.data_dir, 'cifar-10-batches-bin')
    images, labels = cifar10_input.distorted_inputs(data_dir=data_dir,
                                                    batch_size=FLAGS.batch_size)
    if FLAGS.use_fp16:
        images = tf.cast(images, tf.float16)
        labels = tf.cast(labels, tf.float16)
    return images, labels


def inputs(eval_data):
    """Construct input for CIFAR evaluation using the Reader ops.

    Args:
      eval_data: bool, indicating if one should use the train or eval data set.

    Returns:
      images: Images. 4D tensor of [batch_size, IMAGE_SIZE, IMAGE_SIZE, 3] size.
      labels: Labels. 1D tensor of [batch_size] size.

    Raises:
      ValueError: If no data_dir
    """
    if not FLAGS.data_dir:
        raise ValueError('Please supply a data_dir')
    data_dir = os.path.join(FLAGS.data_dir, 'cifar-10-batches-bin')
    images, labels = cifar10_input.inputs(eval_data=eval_data,
                                          data_dir=data_dir,
                                          batch_size=FLAGS.batch_size)
    if FLAGS.use_fp16:
        images = tf.cast(images, tf.float16)
        labels = tf.cast(labels, tf.float16)
    return images, labels

def conv2d(features, kernel_size, stride, out_channel):
    dtype = _get_dtype()
    initializer = tf.truncated_normal_initializer(stddev=5e-2, dtype=dtype)
    regularizer = tf.contrib.layers.l2_regularizer(FLAGS.wd)
    features = tf.layers.conv2d(
            features, filters=out_channel, kernel_size=kernel_size, strides=stride, padding='SAME',
            use_bias=False, kernel_initializer=initializer, kernel_regularizer=regularizer)
    return features


def conv_bn(features, kernel_sizes, strides, out_channels, training, scope):

    dtype = _get_dtype()
    initializer = tf.truncated_normal_initializer(stddev=5e-2, dtype=dtype)
    regularizer = tf.contrib.layers.l2_regularizer(FLAGS.wd)

    for kernel_size, stride, out_channel in zip(kernel_sizes, strides, out_channels):
        features = tf.layers.conv2d(
                features, filters=out_channel, kernel_size=kernel_size, strides=stride, padding='SAME',
                use_bias=False, kernel_initializer=initializer, kernel_regularizer=regularizer)

    bn = tf.layers.batch_normalization(
        features, momentum=FLAGS.bn_momentum, training=training)
    conv = tf.nn.relu(bn, name=scope.name)
    _activation_summary(conv)

    return conv


def dense_bn(features, out_dims, training, scope):
    features = tf.reshape(features, [FLAGS.batch_size, -1])

    dtype = _get_dtype()
    initializer = tf.truncated_normal_initializer(stddev=5e-2, dtype=dtype)
    regularizer = tf.contrib.layers.l2_regularizer(FLAGS.wd)

    for out_dim in out_dims:
        features = tf.layers.dense(
                features, units=out_dim, use_bias=False, kernel_initializer=initializer, kernel_regularizer=regularizer)

    bn = tf.layers.batch_normalization(
        features, momentum=FLAGS.bn_momentum, training=training)
    dense = tf.nn.relu(bn, name=scope.name)
    _activation_summary(dense)

    return dense


def unit_layer(features, out_channel, training):
    bn = tf.layers.batch_normalization(
        features, momentum=FLAGS.bn_momentum, training=training)
    activate = tf.nn.relu(bn)
    conv = conv2d(activate, kernel_size=3, stride=1, out_channel=out_channel)
    drop = tf.layers.dropout(conv, rate=FLAGS.dropout_rate, training=training)
    return drop


def block(features, depth, growth_rate, training):
    for i in range(depth):
        with tf.variable_scope("dense_layer{}".format(i)) as scope:
            unit = unit_layer(features, out_channel=growth_rate, training=training)
            features = tf.concat([features, unit], axis=3, name=scope.name)
    return features


def transition(features, training, scope):
    num_channel = features.get_shape()[3].value
    unit = unit_layer(features, out_channel=num_channel, training=training)
    features = tf.layers.average_pooling2d(
            unit, pool_size=2, strides=2, padding='valid', name=scope.name)
    return features


def global_average_pooling2d(features):
    return tf.reduce_mean(features, axis=[1, 2])


def inference(images, training=True):
    """Build the CIFAR-10 model.

    Args:
      images: Images returned from distorted_inputs() or inputs().

    Returns:
      Logits.
    """

    with tf.variable_scope('conv0') as scope:
        conv0 = conv2d(images, kernel_size=3, stride=1, out_channel=16)

    with tf.variable_scope('dense1') as scope:
        first_depth = int((FLAGS.depth - 4) / 3)
        block1 = block(conv0, first_depth, FLAGS.growth_rate, training)
        dense1 = transition(block1, training, scope=scope)

    with tf.variable_scope('dense2') as scope:
        second_depth = int((FLAGS.depth - 4) / 3)
        block2 = block(dense1, second_depth, FLAGS.growth_rate, training)
        dense2 = transition(block2, training, scope=scope)

    with tf.variable_scope('dense3') as scope:
        third_depth = FLAGS.depth - (4 + first_depth + second_depth)
        block3 = block(dense2, third_depth, FLAGS.growth_rate, training)
        dense3 = transition(block3, training, scope=scope)

    with tf.variable_scope('last') as scope:
        bn = tf.layers.batch_normalization(
            dense3, momentum=FLAGS.bn_momentum, training=training)
        activate = tf.nn.relu(bn)
        last = global_average_pooling2d(activate)

    with tf.variable_scope('softmax_linear') as scope:
        dtype = _get_dtype()
        dim = np.prod(last.get_shape().as_list()[1:])
        kernel_initializer = tf.truncated_normal_initializer(stddev=float(1)/dim, dtype=dtype)
        bias_initializer = tf.zeros_initializer(dtype=dtype)
        regularizer = tf.contrib.layers.l2_regularizer(FLAGS.wd)
        softmax_linear = tf.layers.dense(
                last, units=NUM_CLASSES, use_bias=True,
                kernel_initializer=kernel_initializer,
                bias_initializer=bias_initializer,
                kernel_regularizer=regularizer,
                bias_regularizer=regularizer)
        _activation_summary(softmax_linear)

    return softmax_linear


def loss(logits, labels):
    """Add L2Loss to all the trainable variables.

    Add summary for "Loss" and "Loss/avg".
    Args:
      logits: Logits from inference().
      labels: Labels from distorted_inputs or inputs(). 1-D tensor
              of shape [batch_size]

    Returns:
      Loss tensor of type float.
    """
    # Calculate the average cross entropy loss across the batch.
    labels = tf.cast(labels, tf.int64)
    cross_entropy = tf.nn.sparse_softmax_cross_entropy_with_logits(
        labels=labels, logits=logits, name='cross_entropy_per_example')
    cross_entropy_mean = tf.reduce_mean(cross_entropy, name='cross_entropy')

    tf.get_collection_ref('losses').extend(
            tf.get_collection(tf.GraphKeys.REGULARIZATION_LOSSES))
    tf.get_collection_ref('losses').append(cross_entropy_mean)

    # The total loss is defined as the cross entropy loss plus all of the weight
    # decay terms (L2 loss).
    return tf.add_n(tf.get_collection('losses'), name='total_loss')


def _add_loss_summaries(total_loss):
    """Add summaries for losses in CIFAR-10 model.

    Generates moving average for all losses and associated summaries for
    visualizing the performance of the network.

    Args:
      total_loss: Total loss from loss().
    Returns:
      loss_averages_op: op for generating moving averages of losses.
    """
    # Compute the moving average of all individual losses and the total loss.
    loss_averages = tf.train.ExponentialMovingAverage(0.9, name='avg')
    losses = tf.get_collection('losses')
    loss_averages_op = loss_averages.apply(losses + [total_loss])

    # Attach a scalar summary to all individual losses and the total loss; do the
    # same for the averaged version of the losses.
    for l in losses + [total_loss]:
        # Name each loss as '(raw)' and name the moving average version of the loss
        # as the original loss name.
        tf.summary.scalar(l.op.name + ' (raw)', l)
        tf.summary.scalar(l.op.name, loss_averages.average(l))

    return loss_averages_op


def train(total_loss, global_step):
    """Train CIFAR-10 model.

    Create an optimizer and apply to all trainable variables. Add moving
    average for all trainable variables.

    Args:
      total_loss: Total loss from loss().
      global_step: Integer Variable counting the number of training steps
        processed.
    Returns:
      train_op: op for training.
    """
    # Variables that affect learning rate.
    num_batches_per_epoch = NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN / FLAGS.batch_size
    decay_steps = int(num_batches_per_epoch * NUM_EPOCHS_PER_DECAY)

    # Decay the learning rate based on the number of epochs.
    num_epoch = global_step * FLAGS.batch_size / NUM_EXAMPLES_PER_EPOCH_FOR_TRAIN
    lr_boundaries = [np.array(b, dtype=np.float64) for b in FLAGS.lr_boundaries.split(',')]
    lr_values = [float(v) for v in FLAGS.lr_values.split(',')]
    assert len(lr_boundaries)+1 == len(lr_values), "len(lr_boundaries)+1 must be equal to len(lr_values)"

    lr = tf.train.piecewise_constant(num_epoch, boundaries=lr_boundaries, values=lr_values)
    tf.summary.scalar('learning_rate', lr)

    # Generate moving averages of all losses and associated summaries.
    loss_averages_op = _add_loss_summaries(total_loss)

    # Compute gradients.
    with tf.control_dependencies([loss_averages_op]):
        opt = tf.train.GradientDescentOptimizer(lr)
        grads = opt.compute_gradients(total_loss)

    # Apply gradients.
    apply_gradient_op = opt.apply_gradients(grads, global_step=global_step)

    # Add histograms for trainable variables.
    for var in tf.trainable_variables():
        tf.summary.histogram(var.op.name, var)

    # Add histograms for gradients.
    for grad, var in grads:
        if grad is not None:
            tf.summary.histogram(var.op.name + '/gradients', grad)

    # Track the moving averages of all trainable variables.
    variable_averages = tf.train.ExponentialMovingAverage(
        MOVING_AVERAGE_DECAY, global_step)
    variables_averages_op = variable_averages.apply(tf.trainable_variables())

    # This operation updates moving_mean and moving_variance of
    # batch_normalization.
    update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)

    with tf.control_dependencies([apply_gradient_op, variables_averages_op] + update_ops):
        train_op = tf.no_op(name='train')

    return train_op


def maybe_download_and_extract():
    """Download and extract the tarball from Alex's website."""
    dest_directory = FLAGS.data_dir
    if not os.path.exists(dest_directory):
        os.makedirs(dest_directory)
    filename = DATA_URL.split('/')[-1]
    filepath = os.path.join(dest_directory, filename)
    if not os.path.exists(filepath):
        def _progress(count, block_size, total_size):
            sys.stdout.write('\r>> Downloading %s %.1f%%' % (filename,
                                                             float(count * block_size) / float(total_size) * 100.0))
            sys.stdout.flush()
        filepath, _ = urllib.request.urlretrieve(DATA_URL, filepath, _progress)
        print()
        statinfo = os.stat(filepath)
        print('Successfully downloaded', filename, statinfo.st_size, 'bytes.')
    extracted_dir_path = os.path.join(dest_directory, 'cifar-10-batches-bin')
    if not os.path.exists(extracted_dir_path):
        tarfile.open(filepath, 'r:gz').extractall(dest_directory)
