"""Code for animation the motion prediction."""

import logging

import matplotlib.pyplot as plt
import numpy as np
import h5py

from utils.viz import Ax3DPose
from utils.forward_kinematics import kinematic_tree_variables
from utils.forward_kinematics import revert_coordinate_space
from utils.forward_kinematics import fkl
from parsers import animation_parser

# Load parser arguments
args = animation_parser()


def animate(args=args):
    """Animate the pose.

    Parameters
    ----------
    args : argparse.Namespace
        Arguments from the parser.
    """

    # Logging
    logging.basicConfig(format='%(levelname)s: %(message)s', level=20)

    # Load all the data
    parent, offset, rot_ind, expmap_ind = kinematic_tree_variables()
    with h5py.File('samples.h5', 'r') as h5f:
        # Ground truth (exponential map)
        expmap_gt = h5f['expmap/gt/walking_{}'.format(args.sample_id)][:]
        # Prediction (exponential map)
        expmap_pred = h5f['expmap/preds/walking_{}'.format(args.sample_id)][:]

    # Number of Ground truth/Predicted frames
    nframes_gt, nframes_pred = expmap_gt.shape[0], expmap_pred.shape[0]
    logging.info(f'{nframes_gt} {nframes_pred}')

    # Put them together and revert the coordinate space
    expmap_all = revert_coordinate_space(np.vstack((expmap_gt, expmap_pred)),
                                         np.eye(3), np.zeros(3))
    expmap_gt = expmap_all[:nframes_gt, :]
    expmap_pred = expmap_all[nframes_gt:, :]

    # Use forward kinematics to compute 33 3d points for each frame
    xyz_gt, xyz_pred = np.zeros((nframes_gt, 96)), np.zeros((nframes_pred, 96))

    for i in range(nframes_gt):
        xyz_gt[i, :] = fkl(expmap_gt[i, :], parent, offset, rot_ind,
                           expmap_ind)

    for i in range(nframes_pred):
        xyz_pred[i, :] = fkl(expmap_pred[i, :], parent, offset, rot_ind,
                             expmap_ind)

    # === Plot and animate ===
    fig = plt.figure()
    ax = plt.axes(projection='3d')
    ob = Ax3DPose(ax)

    # First, plot the conditioning ground truth
    for i in range(nframes_gt):
        ob.update(xyz_gt[i, :], lcolor='#ff0000', rcolor='#0000ff')
        plt.show(block=False)
        plt.title('Observations')
        fig.canvas.draw()
        plt.pause(0.01)

    # Plot the prediction
    for i in range(nframes_pred):
        ob.update(xyz_pred[i, :], lcolor='#9b59b6', rcolor='#2ecc71')
        plt.show(block=False)
        plt.title('Predictions')
        fig.canvas.draw()
        plt.pause(0.01)


if __name__ == '__main__':
    animate()
