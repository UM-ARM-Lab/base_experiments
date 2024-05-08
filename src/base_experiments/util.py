import matplotlib
import logging
import os
import torch

import numpy as np

from itar_experiments.env.env import logger


def move_figure(f, x, y):
    """Move figure's upper left corner to pixel (x, y)"""
    backend = matplotlib.get_backend()
    if backend == 'TkAgg':
        f.canvas.manager.window.wm_geometry("+%d+%d" % (x, y))
    elif backend == 'WXAgg':
        f.canvas.manager.window.SetPosition((x, y))
    else:
        # This works for QT and GTK
        # You can also use window.setGeometry
        f.canvas.manager.window.move(x, y)


class MakedirsFileHandler(logging.FileHandler):
    def __init__(self, filename, *args, **kwargs):
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        logging.FileHandler.__init__(self, filename, *args, **kwargs)


def evaluate_action(dstate, actual_dstate):
    if torch.is_tensor(dstate):
        dstate = dstate.cpu().numpy()
    if torch.is_tensor(actual_dstate):
        actual_dstate = actual_dstate.cpu().numpy()
    # evaluate how well we moved relative to how much we wanted to move
    err = actual_dstate - dstate
    err_ratio = np.linalg.norm(err) / (np.linalg.norm(dstate) + 1e-8)
    logger.info(f"action err ratio: {err_ratio:.4f}")
