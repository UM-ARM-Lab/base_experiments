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


def evaluate_action(dstate, actual_dstate, do_print=True, eps=1e-5):
    if torch.is_tensor(dstate):
        dstate = dstate.cpu().numpy()
    if torch.is_tensor(actual_dstate):
        actual_dstate = actual_dstate.cpu().numpy()
    # evaluate how well we moved relative to how much we wanted to move
    err = actual_dstate - dstate
    # if dstate is actually 0 then we make a special case and report the absolute deviation instead of ratio
    if np.linalg.norm(dstate) < eps:
        err_ratio = np.linalg.norm(err)
    else:
        err_ratio = np.linalg.norm(err) / (np.linalg.norm(dstate) + eps)
    if do_print:
        logger.info(f"action err ratio: {err_ratio:.4f} dstate: {dstate} actual_dstate: {actual_dstate}")
    return err_ratio
