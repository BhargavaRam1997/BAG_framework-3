# -*- coding: utf-8 -*-
########################################################################################################################
#
# Copyright (c) 2014, Regents of the University of California
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification, are permitted provided that the
# following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following
#   disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the
#    following disclaimer in the documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES,
# INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
########################################################################################################################

"""This module defines the RoutingGrid class.
"""
from __future__ import (absolute_import, division,
                        print_function, unicode_literals)
# noinspection PyUnresolvedReferences,PyCompatibility
from builtins import *

from typing import Union, Tuple, List, Optional

import numpy as np

from ..util import BBox
from bag.util.search import BinaryIterator


def _gcd(a, b):
    """Compute greater common divisor of two positive integers."""
    while b:
        a, b = b, a % b
    return a


def _lcm(arr, init=1):
    """Compute least common multiple of all numbers in the given list.

    Parameters
    ----------
    arr : Iterable[int]
        a list of integers.
    init : int
        the initial LCM.  Defaults to 1.
    """
    cur_lcm = init
    for val in arr:
        cur_lcm = cur_lcm * val // _gcd(cur_lcm, val)
    return cur_lcm


class RoutingGrid(object):
    """A class that represents the routing grid.

    This class provides various methods to convert between Cartesian coordinates and
    routing tracks.  This class assumes the lower-left coordinate is (0, 0)

    the track numbers are at half-track pitch.  That is, even track numbers corresponds
    to physical tracks, and odd track numbers corresponds to middle between two tracks.
    This convention is chosen so it is easy to locate a via for 2-track wide wires, for
    example.

    Assumptions:

    1. the pitch of all layers evenly divides the largest pitch.

    Parameters
    ----------
    tech_info : bag.layout.core.TechInfo
        the TechInfo instance used to create metals and vias.
    layers : list[int]
        list of available routing layers.  Must be in increasing order.
    spaces : list[float]
        list of track spacings for each layer.
    widths : list[float]
        list of minimum track widths for each layer.
    bot_dir : str
        the direction of the bottom-most layer.  Either 'x' for horizontal tracks or 'y' for
        vertical tracks.
    max_num_tr : int or list[int]
        maximum track width in number of tracks.  Can be given as an integer (which applies to
        all layers), our a list to specify maximum width per layer.
    """

    def __init__(self, tech_info, layers, spaces, widths, bot_dir, max_num_tr=100):
        # error checking
        num_layer = len(layers)
        if len(spaces) != num_layer:
            raise ValueError('spaces length = %d != %d' % (len(spaces), num_layer))
        if len(widths) != num_layer:
            raise ValueError('spaces length = %d != %d' % (len(widths), num_layer))
        if isinstance(max_num_tr, int):
            max_num_tr = [max_num_tr] * num_layer
        elif len(max_num_tr) != num_layer:
            raise ValueError('max_num_tr length = %d != %d' % (len(max_num_tr), num_layer))

        self._tech_info = tech_info
        self._resolution = tech_info.resolution
        self._layout_unit = tech_info.layout_unit
        self.layers = []
        self.sp_tracks = {}
        self.w_tracks = {}
        self.offset_tracks = {}
        self.dir_tracks = {}
        self.max_num_tr_tracks = {}
        self.block_pitch = {}

        cur_dir = bot_dir
        for lay, sp, w, max_num in zip(layers, spaces, widths, max_num_tr):
            self.add_new_layer(lay, sp, w, cur_dir, max_num_tr=max_num)
            # alternate track direction
            cur_dir = 'y' if cur_dir == 'x' else 'x'

        self.update_block_pitch()

    def update_block_pitch(self):
        """Update block pitch."""
        pitch_list = []
        for lay in self.layers:
            cur_blk_pitch = self.sp_tracks[lay] + self.w_tracks[lay]
            cur_dir = self.dir_tracks[lay]
            if pitch_list:
                # the pitch of each layer = LCM of all layers below with same direction
                bot_pitch_iter = (p for idx, p in enumerate(pitch_list) if
                                  self.dir_tracks[self.layers[idx]] == cur_dir)
                cur_blk_pitch = _lcm(bot_pitch_iter, init=cur_blk_pitch)
            pitch_list.append(cur_blk_pitch)
            self.block_pitch[lay] = cur_blk_pitch
            # print('block pitch %d = %d' % (lay, cur_blk_pitch))

    @property
    def tech_info(self):
        """The TechInfo technology object."""
        return self._tech_info

    @property
    def resolution(self):
        """Returns the grid resolution."""
        return self._resolution

    @property
    def layout_unit(self):
        """Returns the layout unit length, in meters."""
        return self._layout_unit

    def get_direction(self, layer_id):
        """Returns the track direction of the given layer.

        Parameters
        ----------
        layer_id : int
            the layer ID.

        Returns
        -------
        tdir : str
            'x' for horizontal tracks, 'y' for vertical tracks.
        """
        return self.dir_tracks[layer_id]

    def get_track_pitch(self, layer_id, unit_mode=False):
        """Returns the routing track pitch on the given layer.

        Parameters
        ----------
        layer_id : int
            the routing layer ID.
        unit_mode : bool
            True to return block pitch in resolution units.

        Returns
        -------
        track_pitch : float
            the track pitch in layout units.
        """
        pitch = self.w_tracks[layer_id] + self.sp_tracks[layer_id]
        return pitch if unit_mode else pitch * self._resolution

    def get_track_width(self, layer_id, width_ntr):
        """Calculate track width in layout units from number of tracks.

        Parameters
        ----------
        layer_id : int
            the track layer ID
        width_ntr : int
            the track width in number of tracks.

        Returns
        -------
        width : float
            the track width in layout units.
        """
        w = self.w_tracks[layer_id]
        sp = self.sp_tracks[layer_id]
        return (width_ntr * (w + sp) - sp) * self._resolution

    def get_num_tracks(self, size, layer_id):
        # type: (Tuple[int, int, int], int) -> int
        """Returns the number of tracks on the given layer for a block with the given size.

        Parameters
        ----------
        size : Tuple[int, int, int]
            the block size tuple.
        layer_id : int
            the layer ID.

        Returns
        -------
        num_tracks : int
            number of tracks on that given layer.
        """
        tr_dir = self.get_direction(layer_id)
        blk_w, blk_h = self.get_size_dimension(size, unit_mode=True)
        tr_pitch = self.w_tracks[layer_id] + self.sp_tracks[layer_id]
        if tr_dir == 'x':
            return blk_h // tr_pitch
        else:
            return blk_w // tr_pitch

    def get_min_length(self, layer_id, width_ntr, unit_mode=False):
        # type: (int, int, bool) -> Union[float, int]
        """Returns the minimum length for the given track.

        Parameters
        ----------
        layer_id : int
            the track layer ID
        width_ntr : int
            the track width in number of tracks.
        unit_mode : bool
            True to return the minimum length in resolution units.

        Returns
        -------
        min_length : Union[float, int]
            the minimum length.
        """
        layer_name = self.tech_info.get_layer_name(layer_id)
        if isinstance(layer_name, tuple):
            layer_name = layer_name[0]
        layer_type = self.tech_info.get_layer_type(layer_name)

        width = self.get_track_width(layer_id, width_ntr)
        min_length = self.tech_info.get_min_length(layer_type, width)

        if unit_mode:
            return int(round(min_length / self._resolution))
        else:
            return min_length

    def get_num_space_tracks(self, layer_id, width_ntr, half_space=False):
        """Returns the number of tracks needed to reserve for space around a track of the given width.

        In advance technologies, metal spacing is often a function of the metal width, so for a
        a wide track we may need to reserve empty tracks next to this.  This method computes the
        minimum number of empty tracks needed.

        Parameters
        ----------
        layer_id : int
            the track layer ID
        width_ntr : int
            the track width in number of tracks.
        half_space : bool
            True to allow half-integer spacing.

        Returns
        -------
        num_sp_tracks : float or int
            minimum space needed around the given track in number of tracks.
        """
        layer_name = self.tech_info.get_layer_name(layer_id)
        if isinstance(layer_name, tuple):
            layer_name = layer_name[0]
        layer_type = self.tech_info.get_layer_type(layer_name)

        width = self.get_track_width(layer_id, width_ntr)
        sp_min = self.tech_info.get_min_space(layer_type, width)
        sp_min_unit = int(round(sp_min / self._resolution))
        w_unit = self.w_tracks[layer_id]
        sp_unit = self.sp_tracks[layer_id]
        half_pitch = (w_unit + sp_unit) // 2
        num_half_pitch = -(-(sp_min_unit - sp_unit) // half_pitch)
        if num_half_pitch % 2 == 0:
            return num_half_pitch // 2
        elif half_space:
            return num_half_pitch / 2.0
        else:
            return (num_half_pitch + 1) // 2

    def get_max_track_width(self, layer_id, num_tracks, tot_space, half_end_space=False):
        # type: (int, int, int) -> int
        """Compute maximum track width and space that satisfies DRC rule.

        Given available number of tracks and numbers of tracks needed, returns
        the maximum possible track width and spacing.

        Parameters
        ----------
        layer_id : int
            the track layer ID.
        num_tracks : int
            number of tracks to draw.
        tot_space : int
            avilable number of tracks.
        half_end_space : bool
            True if end spaces can be half of minimum spacing.  This is true if you're
            these tracks will be repeated, or there are no adjacent tracks.

        Returns
        -------
        tr_w : int
            track width.
        """
        bin_iter = BinaryIterator(1, None)
        num_space = num_tracks if half_end_space else num_tracks + 1
        while bin_iter.has_next():
            tr_w = bin_iter.get_next()
            tr_sp = self.get_num_space_tracks(layer_id, tr_w, half_space=False)
            used_tracks = tr_w * num_tracks + tr_sp * num_space
            if used_tracks > tot_space:
                bin_iter.down()
            else:
                bin_iter.save()
                bin_iter.up()

        opt_w = bin_iter.get_last_save()
        return opt_w

    @staticmethod
    def get_evenly_spaced_tracks(num_tracks, tot_space, track_width, half_end_space=False):
        # type: (int, int, int) -> List[Union[float, int]]
        """Evenly space given number of tracks in the available space.

        Currently this method may return half-integer tracks.

        Parameters
        ----------
        num_tracks : int
            number of tracks to draw.
        tot_space : int
            avilable number of tracks.
        track_width : int
            track width in number of tracks.
        half_end_space : bool
            True if end spaces can be half of minimum spacing.  This is true if you're
            these tracks will be repeated, or there are no adjacent tracks.

        Returns
        -------
        idx_list : List[float]
            list of track indices.  0 is the left-most track.
        """
        if half_end_space:
            # half indices = round((2 * k + 1) * N / (2 * m)) = floor(((2 * k + 1) * N + m) / (2 * m))
            tot_space_htr = 2 * tot_space
            scale = 2 * tot_space_htr
            offset = tot_space_htr + num_tracks
            den = 2 * num_tracks
        else:
            tot_space_htr = 2 * tot_space
            width_htr = 2 * track_width - 2
            # magic math.  You can work it out
            scale = 2 * (tot_space_htr + width_htr)
            offset = 2 * tot_space_htr - width_htr * (num_tracks - 1) + (num_tracks + 1)
            den = 2 * (num_tracks + 1)
        hidx_arr = (scale * np.arange(num_tracks, dtype=int) + offset) // den
        # convert from half indices to actual indices
        idx_list = ((hidx_arr - 1) / 2.0).tolist()  # type: List[float]
        return idx_list

    def get_block_pitch(self, layer_id, unit_mode=False):
        # type: (int, bool) -> Union[float, int]
        """Returns the routing block pitch on the given layer.

        A block pitch always contain an integer number of tracks for all layer
        on or below this block with the same track direction.

        Parameters
        ----------
        layer_id : int
            the routing layer ID.
        unit_mode : bool
            True to return block pitch in resolution units.

        Returns
        -------
        block_pitch : Union[float, int]
            the block pitch in layout units.
        """
        pitch_unit = self.block_pitch[layer_id]
        return pitch_unit if unit_mode else pitch_unit * self.resolution

    def get_block_size(self, layer_id, unit_mode=False):
        # type: (int, bool) -> Tuple[Union[float, int], Union[float, int]]
        """Returns unit block size given the top routing layer.

        Parameters
        ----------
        layer_id : int
            the routing layer ID.
        unit_mode : bool
            True to return block dimension in resolution units.

        Returns
        -------
        block_width : Union[float, int]
            the block width in layout units.
        block_height : Union[float, int]
            the block height in layout units.
        """
        h_pitch = self.block_pitch[layer_id]
        w_pitch = self.block_pitch[layer_id - 1]
        if self.dir_tracks[layer_id] == 'y':
            h_pitch, w_pitch = w_pitch, h_pitch

        if unit_mode:
            return w_pitch, h_pitch
        else:
            return w_pitch * self.resolution, h_pitch * self.resolution

    def get_size_dimension(self, size, unit_mode=False):
        # type: (Tuple[int, int, int]) -> Tuple[Union[float, int], Union[float, int]]
        """Compute width and height from given size.

        Parameters
        ----------
        size : Tuple[int, int, int]
            size of a block, in (top_layer, nx_blk, ny_blk) format.
        unit_mode : bool
            True to return width/height in resolution units.

        Returns
        -------
        width : Union[float, int]
            the width in layout units.
        height : Union[float, int]
            the height in layout units.
        """
        top_layer_id = size[0]
        h_pitch = self.get_block_pitch(top_layer_id, unit_mode=True)
        w_pitch = self.get_block_pitch(top_layer_id - 1, unit_mode=True)
        if self.get_direction(top_layer_id) == 'y':
            h_pitch, w_pitch = w_pitch, h_pitch

        w_unit, h_unit = size[1] * w_pitch, size[2] * h_pitch

        if unit_mode:
            return w_unit, h_unit
        else:
            return w_unit * self.resolution, h_unit * self.resolution

    def get_track_info(self, layer_id):
        """Returns the routing track width and spacing on the given layer.

        Parameters
        ----------
        layer_id : int
            the routing layer ID.

        Returns
        -------
        track_width : float
            the track width in layout units.
        track_spacing : float
            the track spacing in layout units
        """
        return self.w_tracks[layer_id] * self._resolution, self.sp_tracks[layer_id] * self._resolution

    def get_layer_name(self, layer_id, tr_idx):
        """Returns the layer name of the given track.

        Parameters
        ----------
        layer_id : int
            the layer ID.
        tr_idx : float
            the track index.
        """
        layer_name = self.tech_info.get_layer_name(layer_id)
        if isinstance(layer_name, tuple):
            # round down half integer track
            return layer_name[int(tr_idx) % 2]
        else:
            return layer_name

    def get_wire_bounds(self, layer_id, tr_idx, width=1, unit_mode=False):
        # type: (int, float, int, bool) -> Tuple[Union[float, int], Union[float, int]]
        """Calculate the wire bounds coordinate.

        Parameters
        ----------
        layer_id : int
            the layer ID.
        tr_idx : flaot
            the center track index.
        width : int
            width of wire in number of tracks.
        unit_mode : bool
            True to return coordinates in resolution units.

        Returns
        -------
        lower : Union[float, int]
            the lower bound coordinate perpendicular to wire direction.
        upper : Union[float, int]
            the upper bound coordinate perpendicular to wire direction.
        """
        w = self.w_tracks[layer_id]
        sp = self.sp_tracks[layer_id]
        w_wire = width * w + (width - 1) * sp
        center = int(tr_idx * (w + sp)) + self.offset_tracks[layer_id]
        w_half = w_wire // 2
        if unit_mode:
            return center - w_half, center + w_half
        else:
            return (center - w_half) * self._resolution, (center + w_half) * self._resolution

    def get_bbox(self, layer_id, tr_idx, lower, upper, width=1):
        """Compute bounding box for the given wire.

        Parameters
        ----------
        layer_id : int
            the layer ID.
        tr_idx : flaot
            the center track index.
        lower : float
            the lower coordinate along track direction.
        upper : float
            the upper coordinate along track direction.
        width : int
            width of wire in number of tracks.

        Returns
        -------
        bbox : bag.layout.util.BBox
            the bounding box.
        """
        cl, cu = self.get_wire_bounds(layer_id, tr_idx, width=width)
        if self.get_direction(layer_id) == 'x':
            bbox = BBox(lower, cl, upper, cu, self._resolution)
        else:
            bbox = BBox(cl, lower, cu, upper, self._resolution)

        return bbox

    def get_min_track_width(self, layer_id, idc=0, iac_rms=0, iac_peak=0, l=-1,
                            bot_w=-1, top_w=-1, **kwargs):
        """Returns the minimum track width required for the given EM specs.

        Parameters
        ----------
        layer_id : int
            the layer ID.
        idc : float
            the DC current spec.
        iac_rms : float
            the AC RMS current spec.
        iac_peak : float
            the AC peak current spec.
        l : float
            the length of the wire in layout units.  Use negative length
            to disable length enhancement factor.
        bot_w : float
            the bottom layer track width in layout units.  If given, will make sure
            that the via between the two tracks meet EM specs too.
        top_w : float
            the top layer track width in layout units.  If given, will make sure
            that the via between the two tracks meet EM specs too.

        **kwargs :
            override default EM spec parameters.

        Returns
        -------
        track_width : int
            the minimum track width in number of tracks.
        """
        # if double patterning layer, just use any name.
        layer_name = self.tech_info.get_layer_name(layer_id)
        if isinstance(layer_name, tuple):
            layer_name = layer_name[0]
        if bot_w > 0:
            bot_layer_name = self.tech_info.get_layer_name(layer_id - 1)
            if isinstance(bot_layer_name, tuple):
                bot_layer_name = bot_layer_name[0]
        else:
            bot_layer_name = None
        if top_w > 0:
            top_layer_name = self.tech_info.get_layer_name(layer_id + 1)
            if isinstance(top_layer_name, tuple):
                top_layer_name = top_layer_name[0]
        else:
            top_layer_name = None

        # use binary search to find the minimum track width
        bin_iter = BinaryIterator(1, None)
        tr_w = self.w_tracks[layer_id] * self._resolution
        tr_sp = self.sp_tracks[layer_id] * self._resolution
        tr_dir = self.dir_tracks[layer_id]
        bot_dir = 'x' if tr_dir == 'y' else 'y'
        while bin_iter.has_next():
            ntr = bin_iter.get_next()
            width = ntr * (tr_w + tr_sp) - tr_sp
            idc_max, irms_max, ipeak_max = self.tech_info.get_metal_em_specs(layer_name, width, l=l, **kwargs)
            if idc > idc_max or iac_rms > irms_max or iac_peak > ipeak_max:
                # check metal satisfies EM spec
                bin_iter.up()
                continue
            if bot_w > 0:
                if tr_dir == 'x':
                    bbox = BBox(0.0, 0.0, bot_w, width, self._resolution)
                else:
                    bbox = BBox(0.0, 0.0, width, bot_w, self._resolution)
                vinfo = self.tech_info.get_via_info(bbox, bot_layer_name, layer_name, bot_dir, **kwargs)
                if idc > vinfo['idc'] or iac_rms > vinfo['iac_rms'] or iac_peak > vinfo['iac_peak']:
                    bin_iter.up()
                    continue
            if top_w > 0:
                if tr_dir == 'x':
                    bbox = BBox(0.0, 0.0, top_w, width, self._resolution)
                else:
                    bbox = BBox(0.0, 0.0, width, top_w, self._resolution)
                vinfo = self.tech_info.get_via_info(bbox, layer_name, top_layer_name, tr_dir, **kwargs)
                if idc > vinfo['idc'] or iac_rms > vinfo['iac_rms'] or iac_peak > vinfo['iac_peak']:
                    bin_iter.up()
                    continue

            # we got here, so all EM specs passed
            bin_iter.save()
            bin_iter.down()

        return bin_iter.get_last_save()

    def get_track_index_range(self,  # type: RoutingGrid
                              layer_id,  # type: int
                              lower,  # type: Union[float, int]
                              upper,  # type: Union[float, int]
                              num_space=0,  # type: Union[float, int]
                              edge_margin=0,  # type: Union[float, int]
                              half_track=False,  # type: bool
                              unit_mode=False  # type: bool
                              ):
        # type: (...) -> Tuple[Optional[Union[float, int]], Optional[Union[float, int]]]
        """ Returns the first and last track index strictly in the given range.

        Parameters
        ----------
        layer_id : int
            the layer ID.
        lower : Union[float, int]
            the lower coordinate.
        upper : Union[float, int]
            the upper coordinate.
        num_space : Union[float, int]
            number of space tracks to the tracks right outside of the given range.
        edge_margin : Union[float, int]
            minimum space from outer tracks to given range.
        half_track : bool
            True to allow half-integer tracks.
        unit_mode : bool
            True if lower/upper/edge_margin are given in resolution units.

        Returns
        -------
        start_track : Optional[Union[float, int]]
            the first track index.  None if no solution.
        end_track : Optional[Union[float, int]]
            the last track index.  None if no solution.
        """
        if not unit_mode:
            lower = int(round(lower / self._resolution))
            upper = int(round(upper / self._resolution))
            edge_margin = int(round(edge_margin / self._resolution))

        tr_w = self.w_tracks[layer_id]
        tr_sp = self.sp_tracks[layer_id]
        tr_wh = tr_w // 2
        tr_ph = (tr_w + tr_sp) // 2

        # get start track half index
        lower_bnd = self.coord_to_nearest_track(layer_id, lower, half_track=True,
                                                mode=-1, unit_mode=True)
        start_track = self.coord_to_nearest_track(layer_id, lower + edge_margin, half_track=True,
                                                  mode=2, unit_mode=True)
        hstart_track = int(round(2 * max(start_track, lower_bnd + num_space) + 1))
        # check strictly in range
        if hstart_track * tr_ph - tr_wh < lower + edge_margin:
            hstart_track += 1
        # check if half track is allowed
        if not half_track and hstart_track % 2 == 0:
            hstart_track += 1

        # get end track half index
        upper_bnd = self.coord_to_nearest_track(layer_id, upper, half_track=True,
                                                mode=1, unit_mode=True)
        end_track = self.coord_to_nearest_track(layer_id, upper - edge_margin, half_track=True,
                                                mode=-2, unit_mode=True)
        hend_track = int(round(2 * min(end_track, upper_bnd - num_space) + 1))
        # check strictly in range
        if hend_track * tr_ph + tr_wh > upper - edge_margin:
            hend_track -= 1
        # check if half track is allowed
        if not half_track and hend_track % 2 == 0:
            hend_track -= 1

        if hend_track < hstart_track:
            # no solution
            return None, None
        # convert to track
        if hstart_track % 2 == 1:
            start_track = (hstart_track - 1) // 2
        else:
            start_track = (hstart_track - 1) / 2
        if hend_track % 2 == 1:
            end_track = (hend_track - 1) // 2
        else:
            end_track = (hend_track - 1) / 2
        return start_track, end_track

    def get_overlap_tracks(self,  # type: RoutingGrid
                           layer_id,  # type: int
                           lower,  # type: Union[float, int]
                           upper,  # type: Union[float, int]
                           half_track=False,  # type: bool
                           unit_mode=False  # type: bool
                           ):
        # type: (...) -> Tuple[Optional[Union[float, int]], Optional[Union[float, int]]]
        """ Returns the first and last track index that overlaps with the given range.

        Parameters
        ----------
        layer_id : int
            the layer ID.
        lower : Union[float, int]
            the lower coordinate.
        upper : Union[float, int]
            the upper coordinate.
        half_track : bool
            True to allow half-integer tracks.
        unit_mode : bool
            True if lower/upper are given in resolution units.

        Returns
        -------
        start_track : Optional[Union[float, int]]
            the first track index.  None if no solution.
        end_track : Optional[Union[float, int]]
            the last track index.  None if no solution.
        """
        if not unit_mode:
            lower = int(round(lower / self._resolution))
            upper = int(round(upper / self._resolution))

        tr_w = self.w_tracks[layer_id]
        tr_sp = self.sp_tracks[layer_id]
        tr_wh = tr_w // 2
        tr_ph = (tr_w + tr_sp) // 2

        # get start track half index
        lower_bnd = self.coord_to_nearest_track(layer_id, lower, half_track=True,
                                                mode=-1, unit_mode=True)

        hlower_bnd = int(round(2 * lower_bnd + 1))

        # check if overlap
        if hlower_bnd * tr_ph + tr_wh < lower:
            hlower_bnd += 1
        # check if half track is allowed
        if not half_track and hlower_bnd % 2 == 0:
            hlower_bnd += 1

        # get end track half index
        upper_bnd = self.coord_to_nearest_track(layer_id, upper, half_track=True,
                                                mode=1, unit_mode=True)
        hupper_bnd = int(round(2 * upper_bnd + 1))
        # check if overlap
        if hupper_bnd * tr_ph - tr_wh > upper:
            hupper_bnd -= 1
        # check if half track is allowed
        if not half_track and hupper_bnd % 2 == 0:
            hupper_bnd -= 1

        if hupper_bnd < hlower_bnd:
            # no solution
            return None, None
        # convert to track
        if hlower_bnd % 2 == 1:
            start_track = (hlower_bnd - 1) // 2
        else:
            start_track = (hlower_bnd - 1) / 2
        if hupper_bnd % 2 == 1:
            end_track = (hupper_bnd - 1) // 2
        else:
            end_track = (hupper_bnd - 1) / 2
        return start_track, end_track

    def coord_to_track(self, layer_id, coord, unit_mode=False):
        # type: (int, Union[float, int], bool) -> Union[float, int]
        """Convert given coordinate to track number.

        Parameters
        ----------
        layer_id : int
            the layer number.
        coord : Union[float, int]
            the coordinate perpendicular to the track direction.
        unit_mode : bool
            True if coordinate is given in resolution units.

        Returns
        -------
        track : float or int
            the track number
        """
        if not unit_mode:
            coord = int(round(coord / self._resolution))
        pitch = self.sp_tracks[layer_id] + self.w_tracks[layer_id]

        q, r = divmod(coord - self.offset_tracks[layer_id], pitch)

        if r == 0:
            return q
        elif r == (pitch // 2):
            return q + 0.5
        else:
            raise ValueError('coordinate %.4g is not on track.' % coord)

    def find_next_track(self, layer_id, coord, tr_width=1, half_track=False, mode=1):
        """Find the track such that its edges are on the same side w.r.t. the given coordinate.

        Parameters
        ----------
        layer_id : int
            the layer number.
        coord : float
            the coordinate perpendicular to the track direction.
        tr_width : int
            the track width, in number of tracks.
        half_track : bool
            True to allow half integer track center numbers.
        mode : int
            1 to find track with both edge coordinates larger than or equal to the given one,
            -1 to find track with both edge coordinates less than or equal to the given one.

        Returns
        -------
        tr_idx : int or float
            the center track index.
        """
        tr_w = self.get_track_width(layer_id, tr_width)
        if mode > 0:
            return self.coord_to_nearest_track(layer_id, coord + tr_w / 2.0, half_track=half_track,
                                               mode=mode)
        else:
            return self.coord_to_nearest_track(layer_id, coord - tr_w / 2.0, half_track=half_track,
                                               mode=mode)

    def coord_to_nearest_track(self, layer_id, coord, half_track=False, mode=0,
                               unit_mode=False):
        """Returns the track number closest to the given coordinate.

        Parameters
        ----------
        layer_id : int
            the layer number.
        coord : float
            the coordinate perpendicular to the track direction.
        half_track : bool
            if True, allow half integer track numbers.
        mode : int
            the "rounding" mode.

            If mode == 0, return the nearest track (default).

            If mode == -1, return the nearest track with coordinate less
            than or equal to coord.

            If mode == -2, return the nearest track with coordinate less
            than coord.

            If mode == 1, return the nearest track with coordiante greater
            than or equal to coord.

            If mode == 2, return the nearest track with coordinate greater
            than coord.
        unit_mode : bool
            True if the given coordinate is in resolution units.

        Returns
        -------
        track : float or int
            the track number
        """
        if not unit_mode:
            coord = int(round(coord / self._resolution))

        pitch = self.sp_tracks[layer_id] + self.w_tracks[layer_id]
        if half_track:
            pitch //= 2

        q, r = divmod(coord - self.offset_tracks[layer_id], pitch)

        if r == 0:
            # exactly on track
            if mode == -2:
                # move to lower track
                q -= 1
            elif mode == 2:
                # move to upper track
                q += 1
        else:
            # not on track
            if mode > 0 or (mode == 0 and r >= pitch / 2):
                # round up
                q += 1

        if not half_track:
            return q
        elif q % 2 == 0:
            return q // 2
        else:
            return q / 2

    def track_to_coord(self, layer_id, track_idx):
        """Convert given track number to coordinate.

        Parameters
        ----------
        layer_id : int
            the layer number.
        track_idx : float or int
            the track number.

        Returns
        -------
        coord : float
            the coordinate perpendicular to track direction.
        """
        pitch = self.sp_tracks[layer_id] + self.w_tracks[layer_id]
        coord_unit = pitch * track_idx + self.offset_tracks[layer_id]
        return coord_unit * self._resolution

    def interval_to_track(self, layer_id, intv, unit_mode=False):
        # type: (int, Tuple[Union[float, int], Union[float, int]], bool) -> Tuple[Union[float, int], int]
        """Convert given coordinates to track number and width.

        Parameters
        ----------
        layer_id : int
            the layer number.
        intv : Tuple[Union[float, int], Union[float, int]]
            lower and upper coordinates perpendicular to the track direction.
        unit_mode : bool
            True if dimensions are given in resolution units.

        Returns
        -------
        track : Union[float, int]
            the track number
        width : int
            the track width, in number of tracks.
        """
        res = self._resolution
        start, stop = intv
        if not unit_mode:
            start = int(round(start / res))
            stop = int(round(stop / res))

        track = self.coord_to_track(layer_id, (start + stop) // 2, unit_mode=True)
        width = stop - start

        w = self.w_tracks[layer_id]
        pitch = self.sp_tracks[layer_id] + w

        q, r = divmod(width - w, pitch)
        if r != 0:
            raise ValueError('Interval {} on layer {} width not quantized'.format(intv, layer_id))

        return track, q + 1

    def copy(self):
        """Returns a deep copy of this RoutingGrid."""
        cls = self.__class__
        result = cls.__new__(cls)
        attrs = result.__dict__
        attrs['_tech_info'] = self._tech_info
        attrs['_resolution'] = self._resolution
        attrs['_layout_unit'] = self._layout_unit
        attrs['layers'] = list(self.layers)
        attrs['sp_tracks'] = self.sp_tracks.copy()
        attrs['dir_tracks'] = self.dir_tracks.copy()
        attrs['offset_tracks'] = self.offset_tracks.copy()
        attrs['w_tracks'] = self.w_tracks.copy()
        attrs['max_num_tr_tracks'] = self.max_num_tr_tracks.copy()
        attrs['block_pitch'] = self.block_pitch.copy()

        return result

    def add_new_layer(self, layer_id, tr_space, tr_width, direction,
                      max_num_tr=100, share_track=False, override=False):
        """Add a new layer to this RoutingGrid.

        This method is used to add customized routing grid per template on lower level layers.
        The new layers doesn't necessarily need to follow alternating track direction, however,
        if you do this you cannot connect to adjacent level metals.

        Note: do not use this method to add/modify top level layers, so it does not calculate
        block pitch.

        Parameters
        ----------
        layer_id : int
            the new layer ID.
        tr_space : float
            the track spacing, in layout units.
        tr_width : float
            the track width, in layout units.
        direction : string
            track direction.  'x' for horizontal, 'y' for vertical.
        max_num_tr : int
            maximum track width in number of tracks.
        share_track : bool
            True to share track with adjacent blocks.  Defaults to False.
        override : bool
            True to override existing layers if they already exist.
        """
        if layer_id in self.sp_tracks:
            if not override:
                raise ValueError('Layer %d already on routing grid.' % layer_id)
        else:
            self.layers.append(layer_id)
            self.layers.sort()
        sp_unit = 2 * int(round(tr_space / (2 * self.resolution)))
        w_unit = 2 * int(round(tr_width / (2 * self.resolution)))
        self.sp_tracks[layer_id] = sp_unit
        self.w_tracks[layer_id] = w_unit
        self.dir_tracks[layer_id] = direction
        self.max_num_tr_tracks[layer_id] = max_num_tr
        offset = 0 if share_track else (sp_unit + w_unit) // 2
        self.offset_tracks[layer_id] = offset
