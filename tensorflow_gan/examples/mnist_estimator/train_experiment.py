# coding=utf-8
# Copyright 2019 The TensorFlow GAN Authors.
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

"""Trains a GANEstimator on MNIST data using `train_and_evaluate`."""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import os
from absl import app
from absl import flags
import numpy as np
import PIL
from six.moves import xrange  # pylint: disable=redefined-builtin

import tensorflow as tf
import tensorflow_gan as tfgan

from tensorflow_gan.examples.mnist import data_provider
from tensorflow_gan.examples.mnist import networks
from tensorflow_gan.examples.mnist import util


# ML Hparams.
flags.DEFINE_integer('batch_size_mnist_estimator', 32,
                     'The number of images in each batch.')
flags.DEFINE_integer(
    'noise_dims_mnist_exp', 64, 'Dimensions of the generator noise vector')
flags.DEFINE_float('generator_lr_mnist', 0.000076421, 'The generator learning rate.')
flags.DEFINE_float('discriminator_lr_mnist', 0.0031938,
                   'The discriminator learning rate.')
flags.DEFINE_bool('joint_train', False,
                  'Whether to jointly or sequentially train the generator and '
                  'discriminator.')

# ML Infra.
flags.DEFINE_string('model_dir', '/tmp/tfgan_logdir/mnist-estimator-tae',
                    'Optional location to save model. If `None`, use a '
                    'default provided by tf.Estimator.')
flags.DEFINE_integer('num_train_steps', 20000,
                     'The maximum number of gradient steps.')
flags.DEFINE_integer('num_eval_steps', 400,
                     'The number of evaluation steps.')
flags.DEFINE_integer('num_reader_parallel_calls', 4,
                     'Number of parallel calls in the input dataset.')
flags.DEFINE_boolean('use_dummy_data', False,
                     'Whether to use fake data. Used for testing.')

FLAGS = flags.FLAGS


def input_fn(mode, params):
  """Input function for GANEstimator."""
  if 'batch_size_mnist_estimator' not in params:
    raise ValueError('batch_size_mnist_estimator must be in params')
  if 'noise_dims_mnist_exp' not in params:
    raise ValueError('noise_dims_mnist_exp must be in params')
  bs = params['batch_size_mnist_estimator']
  nd = params['noise_dims_mnist_exp']
  split = 'train' if mode == tf.estimator.ModeKeys.TRAIN else 'test'
  shuffle = (mode == tf.estimator.ModeKeys.TRAIN)
  just_noise = (mode == tf.estimator.ModeKeys.PREDICT)

  noise_ds = (tf.data.Dataset.from_tensors(0).repeat()
              .map(lambda _: tf.random_normal([bs, nd])))

  if just_noise:
    return noise_ds

  if FLAGS.use_dummy_data:
    img = np.zeros((bs, 28, 28, 1), dtype=np.float32)
    images_ds = tf.data.Dataset.from_tensors(img).repeat()
  else:
    images_ds = (
        data_provider.provide_dataset(
            split, bs, FLAGS.num_reader_parallel_calls, shuffle)
        .map(lambda x: x['images']))  # Just take the images.

  return tf.data.Dataset.zip((noise_ds, images_ds))


def unconditional_generator(noise, mode):
  """MNIST generator with extra argument for tf.Estimator's `mode`."""
  is_training = (mode == tf.estimator.ModeKeys.TRAIN)
  return networks.unconditional_generator(noise, is_training=is_training)


def get_metrics(gan_model):
  """Return metrics for MNIST experiment."""
  real_mnist_score = util.mnist_score(gan_model.real_data)
  generated_mnist_score = util.mnist_score(gan_model.generated_data)
  frechet_distance = util.mnist_frechet_distance(
      gan_model.real_data, gan_model.generated_data)
  return {
      'real_mnist_score': tf.compat.v1.metrics.mean(real_mnist_score),
      'mnist_score': tf.compat.v1.metrics.mean(generated_mnist_score),
      'frechet_distance': tf.compat.v1.metrics.mean(frechet_distance),
  }


def make_estimator(hparams):
  return tfgan.estimator.GANEstimator(
      model_dir=FLAGS.model_dir,
      generator_fn=unconditional_generator,
      discriminator_fn=networks.unconditional_discriminator,
      generator_loss_fn=tfgan.losses.wasserstein_generator_loss,
      discriminator_loss_fn=tfgan.losses.wasserstein_discriminator_loss,
      params={'batch_size_mnist_estimator': hparams.batch_size_mnist_estimator,
              'noise_dims_mnist_exp': hparams.noise_dims_mnist_exp},
      generator_optimizer=tf.compat.v1.train.AdamOptimizer(
          hparams.generator_lr_mnist, 0.5),
      discriminator_optimizer=tf.compat.v1.train.AdamOptimizer(
          hparams.discriminator_lr_mnist, 0.5),
      add_summaries=tfgan.estimator.SummaryType.IMAGES,
      get_eval_metric_ops_fn=get_metrics)


def write_predictions_to_disk(predictions, out_dir, current_step):
  """Write some inference from the final model to disk."""
  grid_shape = (predictions.shape[0] // 10, 10)
  tiled_image = tfgan.eval.python_image_grid(predictions, grid_shape=grid_shape)
  eval_dir = os.path.join(out_dir, 'outputs')
  if not tf.io.gfile.exists(eval_dir):
    tf.io.gfile.makedirs(eval_dir)
  fn = os.path.join(eval_dir, 'unconditional_gan_%ssteps.png' % current_step)
  with tf.io.gfile.GFile(fn, 'w') as f:
    # Convert tiled_image from float32 in [-1, 1] to unit8 [0, 255].
    img_np = np.squeeze((255 / 2.0) * (tiled_image + 1.0), axis=2)
    pil_image = PIL.Image.fromarray(img_np.astype(np.uint8))
    pil_image.convert('RGB').save(f, 'PNG')
  tf.compat.v1.logging.info('Wrote output to: %s', fn)


def main(_):
  hparams = collections.namedtuple(
      'HParams',
      ['generator_lr_mnist', 'discriminator_lr_mnist', 'joint_train', 'batch_size_mnist_estimator',
       'noise_dims_mnist_exp'])(
           FLAGS.generator_lr_mnist, FLAGS.discriminator_lr_mnist, FLAGS.joint_train,
           FLAGS.batch_size_mnist_estimator, FLAGS.noise_dims_mnist_exp)
  estimator = make_estimator(hparams)
  train_spec = tf.estimator.TrainSpec(
      input_fn=input_fn, max_steps=FLAGS.num_train_steps)
  eval_spec = tf.estimator.EvalSpec(
      name='default', input_fn=input_fn, steps=FLAGS.num_eval_steps)

  # Run training and evaluation for some steps.
  tf.estimator.train_and_evaluate(estimator, train_spec, eval_spec)

  # Generate predictions and write them to disk.
  yields_prediction = estimator.predict(input_fn)
  predictions = np.array([next(yields_prediction) for _ in xrange(100)])
  write_predictions_to_disk(predictions, FLAGS.model_dir, FLAGS.num_train_steps)


if __name__ == '__main__':
  app.run(main)