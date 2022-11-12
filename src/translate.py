
"""Simple code for training an RNN for motion prediction."""
import math
import os
import random
import sys
import h5py
import logging
import numpy as np
from six.moves import xrange # pylint: disable=redefined-builtin

from utils.data_utils import *
from models.seq2seq_model import *
import torch
import torch.optim as optim
from torch.autograd import Variable
import argparse

# Learning
parser = argparse.ArgumentParser(description='Train RNN for human pose estimation')
parser.add_argument('--learning_rate', dest='learning_rate',
                  help='Learning rate',
                  default=0.0005, type=float)
parser.add_argument('--learning_rate_decay_factor', dest='learning_rate_decay_factor',
                  help='Learning rate is multiplied by this much. 1 means no decay.',
                  default=0.95, type=float)
parser.add_argument('--learning_rate_step', dest='learning_rate_step',
                  help='Every this many steps, do decay.',
                  default=10000, type=int)
parser.add_argument('--batch_size', dest='batch_size',
                  help='Batch size to use during training.',
                  default=128, type=int)
parser.add_argument('--iterations', dest='iterations',
                  help='Iterations to train for.',
                  default=1e5, type=int)
parser.add_argument('--test_every', dest='test_every',
                  help='',
                  default=200, type=int)
# Architecture
parser.add_argument('--loss_to_use', dest='loss_to_use',
                  help='The type of loss to use, supervised or sampling_based',
                  default='sampling_based', type=str)
parser.add_argument('--size', dest='size',
                  help='Size of each model layer.',
                  default=1024, type=int)
parser.add_argument('--seq_length_in', dest='seq_length_in',
                  help='Number of frames to feed into the encoder. 25 fps',
                  default=50, type=int)
parser.add_argument('--seq_length_out', dest='seq_length_out',
                  help='Number of frames that the decoder has to predict. 25fps',
                  default=10, type=int)
# Directories
parser.add_argument('--data_dir', dest='data_dir',
                  help='Data directory',
                  default=os.path.normpath("./data/h3.6m/dataset"), type=str)
parser.add_argument('--train_dir', dest='train_dir',
                  help='Training directory',
                  default=os.path.normpath("./experiments/"), type=str)
parser.add_argument('--action', dest='action',
                  help='The action to train on. all means all the actions, all_periodic means walking, eating and smoking',
                  default='all', type=str)
parser.add_argument('--load-model', dest='load_model',
                  help='Try to load a previous checkpoint.',default=0, type=int)
parser.add_argument('--sample', dest='sample',
                  help='Set to True for sampling.', action='store_true',default=False)
parser.add_argument('--log-level',type=int, default=20,help='Log level (default: 20)')
parser.add_argument('--log-file',default='',help='Log file (default: standard output)')
args = parser.parse_args()

train_dir = os.path.normpath(os.path.join( args.train_dir, args.action,
  'out_{0}'.format(args.seq_length_out),
  'iterations_{0}'.format(args.iterations),
  args.loss_to_use,
  'size_{0}'.format(args.size),
  'lr_{0}'.format(args.learning_rate)))

# Logging
if args.log_file=='':
  logging.basicConfig(format='%(levelname)s: %(message)s',level=args.log_level)
else:
  logging.basicConfig(filename=args.log_file,format='%(levelname)s: %(message)s',level=args.log_level)

# Detect device
if torch.cuda.is_available():
  logging.info(torch.cuda.get_device_name(torch.cuda.current_device()))
else:
  logging.info("cpu")
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

logging.info("Train dir: 0"+train_dir)
os.makedirs(train_dir, exist_ok=True)

def create_model(actions, sampling=False):
  """Create translation model and initialize or load parameters in session."""

  model = Seq2SeqModel(
      args.seq_length_in if not sampling else 50,
      args.seq_length_out if not sampling else 100,
      args.size, # hidden layer size
      args.batch_size,
      args.learning_rate,
      args.learning_rate_decay_factor,
      args.loss_to_use if not sampling else "sampling_based",
      len( actions ))

  if args.load_model==0:
    return model

  logging.info("Loading model")
  model = torch.load(train_dir + '/model_' + str(args.load_model))
  if sampling:
    model.source_seq_len = 50
    model.target_seq_len = 100
  return model


def train():
  """Train a seq2seq model on human motion"""
  # Set of actions
  actions           = define_actions( args.action )
  number_of_actions = len( actions )

  train_set, test_set, data_mean, data_std, dim_to_ignore, dim_to_use = read_all_data(
    actions, args.seq_length_in, args.seq_length_out, args.data_dir)

  # Create model
  model = create_model(actions, args.sample)
  model = model.to(device)

  # === Read and denormalize the gt with srnn's seeds, as we'll need them
  # many times for evaluation in Euler Angles ===
  srnn_gts_euler = get_srnn_gts( actions, model, test_set, data_mean,data_std, dim_to_ignore)

  #=== This is the training loop ===
  loss, val_loss = 0.0, 0.0
  current_step = 0 if args.load_model <= 0 else args.load_model + 1
  previous_losses = []

  #optimiser = optim.SGD(model.parameters(), lr=args.learning_rate)
  optimiser = optim.Adam(model.parameters(), lr=args.learning_rate, betas = (0.9, 0.999))

  for _ in range( args.iterations ):
      optimiser.zero_grad()
	  # Set a flag to compute gradients
      model.train()

      # === Training step ===
      encoder_inputs, decoder_inputs, decoder_outputs = model.get_batch(train_set,actions,device)
	  # Forward pass
      preds = model(encoder_inputs, decoder_inputs,device)
      # Loss
      step_loss = (preds-decoder_outputs)**2
      step_loss = step_loss.mean()

      # Backpropagation and update step
      step_loss.backward()
      optimiser.step()

      step_loss = step_loss.cpu().data.numpy()

      if current_step % 10 == 0:
        logging.info("step {0:04d}; step_loss: {1:.4f}".format(current_step, step_loss ))
      loss += step_loss / args.test_every
      current_step += 1
      # === step decay ===
      if current_step % args.learning_rate_step == 0:
        args.learning_rate = args.learning_rate*args.learning_rate_decay_factor
        optimiser = optim.Adam(model.parameters(), lr=args.learning_rate, betas = (0.9, 0.999))
        print("Decay learning rate. New value at " + str(args.learning_rate))

      # Once in a while, we save checkpoint, print statistics, and run evals.
      if current_step % args.test_every == 0:
        model.eval()

        # === Validation with randomly chosen seeds ===
        encoder_inputs, decoder_inputs, decoder_outputs = model.get_batch(test_set,actions,device)

        preds = model(encoder_inputs, decoder_inputs, device)

        step_loss = (preds-decoder_outputs)**2
        step_loss = step_loss.mean()

        val_loss = step_loss # Loss book-keeping

        print()
        print("{0: <16} |".format("milliseconds"), end="")
        for ms in [80, 160, 320, 400, 560, 1000]:
          print(" {0:5d} |".format(ms), end="")
        print()

        # === Validation with srnn's seeds ===
        for action in actions:

          # Evaluate the model on the test batches
          encoder_inputs, decoder_inputs, decoder_outputs = model.get_batch_srnn( test_set, action, device)
          #### Evaluate model on action
          srnn_poses = model(encoder_inputs, decoder_inputs, device)
          srnn_loss = (srnn_poses - decoder_outputs)**2
          srnn_loss.cpu().data.numpy()
          srnn_loss = srnn_loss.mean()

          srnn_poses = srnn_poses.cpu().data.numpy()
          srnn_poses = srnn_poses.transpose([1,0,2])

          srnn_loss = srnn_loss.cpu().data.numpy()
          # Denormalize the output
          srnn_pred_expmap = revert_output_format( srnn_poses,
            data_mean, data_std, dim_to_ignore, actions)

          # Save the errors here
          mean_errors = np.zeros( (len(srnn_pred_expmap), srnn_pred_expmap[0].shape[0]) )

          # Training is done in exponential map, but the error is reported in
          # Euler angles, as in previous work.
          # See https://github.com/asheshjain399/RNNexp/issues/6#issuecomment-247769197
          N_SEQUENCE_TEST = 8
          for i in np.arange(N_SEQUENCE_TEST):
            eulerchannels_pred = srnn_pred_expmap[i]

            # Convert from exponential map to Euler angles
            for j in np.arange( eulerchannels_pred.shape[0] ):
              for k in np.arange(3,97,3):
                eulerchannels_pred[j,k:k+3] = rotmat2euler(expmap2rotmat( eulerchannels_pred[j,k:k+3] ))

            # The global translation (first 3 entries) and global rotation
            # (next 3 entries) are also not considered in the error, so the_key
            # are set to zero.
            # See https://github.com/asheshjain399/RNNexp/issues/6#issuecomment-249404882
            gt_i=np.copy(srnn_gts_euler[action][i])
            gt_i[:,0:6] = 0

            # Now compute the l2 error. The following is numpy port of the error
            # function provided by Ashesh Jain (in matlab), available at
            # https://github.com/asheshjain399/RNNexp/blob/srnn/structural_rnn/CRFProblems/H3.6m/dataParser/Utils/motionGenerationError.m#L40-L54
            idx_to_use = np.where( np.std( gt_i, 0 ) > 1e-4 )[0]

            euc_error = np.power( gt_i[:,idx_to_use] - eulerchannels_pred[:,idx_to_use], 2)
            euc_error = np.sum(euc_error, 1)
            euc_error = np.sqrt( euc_error )
            mean_errors[i,:] = euc_error

          # This is simply the mean error over the N_SEQUENCE_TEST examples
          mean_mean_errors = np.mean( mean_errors, 0 )

          # Pretty print of the results for 80, 160, 320, 400, 560 and 1000 ms
          print("{0: <16} |".format(action), end="")
          for ms in [1,3,7,9,13,24]:
            if args.seq_length_out >= ms+1:
              print(" {0:.3f} |".format( mean_mean_errors[ms] ), end="")
            else:
              print("   n/a |", end="")
          print()

        print()
        print("============================\n"
              "Global step:         %d\n"
              "Learning rate:       %.4f\n"
              "Train loss avg:      %.4f\n"
              "--------------------------\n"
              "Val loss:            %.4f\n"
              "srnn loss:           %.4f\n"
              "============================" % (current_step,
              args.learning_rate, loss,val_loss, srnn_loss))

        torch.save(model, train_dir + '/model_' + str(current_step))

        print()
        previous_losses.append(loss)

        # Reset global time and loss
        step_time, loss = 0, 0

        sys.stdout.flush()


def get_srnn_gts( actions, model, test_set, data_mean, data_std, dim_to_ignore, to_euler=True ):
  """
  Get the ground truths for srnn's sequences, and convert to Euler angles.
  (the error is always computed in Euler angles).

  Args
    actions: a list of actions to get ground truths for.
    model: training model we are using (we only use the "get_batch" method).
    test_set: dictionary with normalized training data.
    data_mean: d-long vector with the mean of the training data.
    data_std: d-long vector with the standard deviation of the training data.
    dim_to_ignore: dimensions that we are not using to train/predict.
    to_euler: whether to convert the angles to Euler format or keep thm in exponential map

  Returns
    srnn_gts_euler: a dictionary where the keys are actions, and the values
      are the ground_truth, denormalized expected outputs of srnns's seeds.
  """
  srnn_gts_euler = {}

  for action in actions:

    srnn_gt_euler = []
    _, _, srnn_expmap = model.get_batch_srnn( test_set, action, device)
    srnn_expmap = srnn_expmap.cpu()
    # expmap -> rotmat -> euler
    for i in np.arange( srnn_expmap.shape[0] ):
      denormed = unNormalizeData(srnn_expmap[i,:,:], data_mean, data_std, dim_to_ignore, actions)

      if to_euler:
        for j in np.arange( denormed.shape[0] ):
          for k in np.arange(3,97,3):
            denormed[j,k:k+3] = rotmat2euler(expmap2rotmat( denormed[j,k:k+3] ))

      srnn_gt_euler.append( denormed );

    # Put back in the dictionary
    srnn_gts_euler[action] = srnn_gt_euler

  return srnn_gts_euler


def sample():
  """Sample predictions for srnn's seeds"""
  actions = define_actions( args.action )

  if True:
    # === Create the model ===
    logging.info("Creating a model with {} units.".format(args.size))
    sampling     = True
    model = create_model(actions, sampling)
    model = model.to(device)
    logging.info("Model created")

    # Load all the data
    train_set, test_set, data_mean, data_std, dim_to_ignore, dim_to_use = read_all_data(
      actions, args.seq_length_in, args.seq_length_out, args.data_dir)

    # === Read and denormalize the gt with srnn's seeds, as we'll need them
    # many times for evaluation in Euler Angles ===
    srnn_gts_expmap = get_srnn_gts( actions, model, test_set, data_mean,
                              data_std, dim_to_ignore, to_euler=False )
    srnn_gts_euler = get_srnn_gts( actions, model, test_set, data_mean,
                              data_std, dim_to_ignore)

    # Clean and create a new h5 file of samples
    SAMPLES_FNAME = 'samples.h5'
    try:
      os.remove( SAMPLES_FNAME )
    except OSError:
      pass

    # Predict and save for each action
    for action in actions:

      # Make prediction with srnn' seeds
      encoder_inputs, decoder_inputs, decoder_outputs = model.get_batch_srnn( test_set, action, device)
      # Forward pass
      srnn_poses = model(encoder_inputs, decoder_inputs)

      srnn_loss = (srnn_poses - decoder_outputs)**2
      srnn_loss.cpu().data.numpy()
      srnn_loss = srnn_loss.mean()

      srnn_poses = srnn_poses.cpu().data.numpy()
      srnn_poses = srnn_poses.transpose([1,0,2])

      srnn_loss = srnn_loss.cpu().data.numpy()
      # denormalizes too
      srnn_pred_expmap = revert_output_format(srnn_poses, data_mean, data_std, dim_to_ignore, actions)

      # Save the samples
      with h5py.File( SAMPLES_FNAME, 'a' ) as hf:
        for i in np.arange(8):
          # Save conditioning ground truth
          node_name = 'expmap/gt/{1}_{0}'.format(i, action)
          hf.create_dataset( node_name, data=srnn_gts_expmap[action][i] )
          # Save prediction
          node_name = 'expmap/preds/{1}_{0}'.format(i, action)
          hf.create_dataset( node_name, data=srnn_pred_expmap[i] )

      # Compute and save the errors here
      mean_errors = np.zeros( (len(srnn_pred_expmap), srnn_pred_expmap[0].shape[0]) )

      for i in np.arange(8):

        eulerchannels_pred = srnn_pred_expmap[i]

        for j in np.arange( eulerchannels_pred.shape[0] ):
          for k in np.arange(3,97,3):
            eulerchannels_pred[j,k:k+3] = rotmat2euler(expmap2rotmat( eulerchannels_pred[j,k:k+3] ))

        eulerchannels_pred[:,0:6] = 0

        # Pick only the dimensions with sufficient standard deviation. Others are ignored.
        idx_to_use = np.where( np.std( eulerchannels_pred, 0 ) > 1e-4 )[0]

        euc_error = np.power( srnn_gts_euler[action][i][:,idx_to_use] - eulerchannels_pred[:,idx_to_use], 2)
        euc_error = np.sum(euc_error, 1)
        euc_error = np.sqrt( euc_error )
        mean_errors[i,:] = euc_error

      mean_mean_errors = np.mean( mean_errors, 0 )
      print( action )
      print( ','.join(map(str, mean_mean_errors.tolist() )) )

      with h5py.File( SAMPLES_FNAME, 'a' ) as hf:
        node_name = 'mean_{0}_error'.format( action )
        hf.create_dataset( node_name, data=mean_mean_errors )

  return


def define_actions( action ):
  """
  Define the list of actions we are using.

  Args
    action: String with the passed action. Could be "all"
  Returns
    actions: List of strings of actions
  Raises
    ValueError if the action is not included in H3.6M
  """

  actions = ["walking", "eating", "smoking", "discussion",  "directions",
              "greeting", "phoning", "posing", "purchases", "sitting",
              "sittingdown", "takingphoto", "waiting", "walkingdog",
              "walkingtogether"]

  if action in actions:
    return [action]
  if action == "all":
    return actions
  if action == "all_srnn":
    return ["walking", "eating", "smoking", "discussion"]
  raise( ValueError, "Unrecognized action: %d" % action )


def read_all_data( actions, seq_length_in, seq_length_out, data_dir):
  """
  Loads data for training/testing and normalizes it.

  Args
    actions: list of strings (actions) to load
    seq_length_in: number of frames to use in the burn-in sequence
    seq_length_out: number of frames to use in the output sequence
    data_dir: directory to load the data from
  Returns
    train_set: dictionary with normalized training data
    test_set: dictionary with test data
    data_mean: d-long vector with the mean of the training data
    data_std: d-long vector with the standard dev of the training data
    dim_to_ignore: dimensions that are not used becaused stdev is too small
    dim_to_use: dimensions that we are actually using in the model
  """

  # === Read training data ===
  logging.info("Reading training data (seq_len_in: {0}, seq_len_out {1}).".format(
           seq_length_in, seq_length_out))

  train_subject_ids = [1,6,7,8,9,11]
  test_subject_ids = [5]

  train_set, complete_train = load_data(data_dir,train_subject_ids,actions)
  test_set,  complete_test  = load_data(data_dir,test_subject_ids, actions)

  # Compute normalization stats
  data_mean, data_std, dim_to_ignore, dim_to_use = normalization_stats(complete_train)

  # Normalize -- subtract mean, divide by stdev
  train_set = normalize_data( train_set, data_mean, data_std, dim_to_use, actions)
  test_set  = normalize_data( test_set,  data_mean, data_std, dim_to_use, actions)
  return train_set, test_set, data_mean, data_std, dim_to_ignore, dim_to_use


def main():
  if args.sample:
    sample()
  else:
    train()

if __name__ == "__main__":
    main()
