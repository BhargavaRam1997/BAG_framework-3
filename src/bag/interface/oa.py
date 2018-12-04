# -*- coding: utf-8 -*-

"""This module implements all CAD database manipulations using OpenAccess plugins.
"""

from typing import TYPE_CHECKING, Sequence, List, Dict, Optional, Any, Tuple

import os
import shutil

import bag.io
from .database import DbAccess
from .skill import handle_reply

try:
    from pybag.core import PyOADatabase
except ImportError:
    raise ImportError('Cannot import pybag library.  Do you have the right shared library file?')

if TYPE_CHECKING:
    from .zmqwrapper import ZMQDealer


class OAInterface(DbAccess):
    """OpenAccess interface between bag and Virtuoso.
    """

    def __init__(self, dealer, tmp_dir, db_config, lib_defs_file):
        # type: (ZMQDealer, str, Dict[str, Any], str) -> None

        # Create PyOADatabase object before calling super constructor,
        # So that schematic library yaml path is added correctly.
        if 'lib_def_path' in db_config:
            cds_lib_path = db_config['lib_def_path']
        elif 'CDSLIBPATH' in os.environ:
            cds_lib_path = os.path.abspath(os.path.join(os.environ['CDSLIBPATH'], 'cds.lib'))
        else:
            cds_lib_path = os.path.abspath('./cds.lib')

        self._oa_db = PyOADatabase(cds_lib_path)
        for lib_name in db_config['schematic']['exclude_libraries']:
            self._oa_db.add_primitive_lib(lib_name)

        DbAccess.__init__(self, dealer, tmp_dir, db_config, lib_defs_file)

    def add_sch_library(self, lib_name):
        """Override; register yaml path in PyOADatabase too."""
        lib_path = DbAccess.add_sch_library(self, lib_name)
        self._oa_db.add_yaml_path(lib_name, os.path.join(lib_path, 'netlist_info'))

    def _eval_skill(self, expr, input_files=None, out_file=None):
        # type: (str, Optional[Dict[str, Any]], Optional[str]) -> str
        """Send a request to evaluate the given skill expression.

        Because Virtuoso has a limit on the input/output data (< 4096 bytes),
        if your input is large, you need to write it to a file and have
        Virtuoso open the file to parse it.  Similarly, if you expect a
        large output, you need to make Virtuoso write the result to the
        file, then read it yourself.  The parameters input_files and
        out_file help you achieve this functionality.

        For example, if you need to evaluate "skill_fun(arg fname)", where
        arg is a file containing the list [1 2 3], and fname is the output
        file name, you will call this function with:

        expr = "skill_fun({arg} {fname})"
        input_files = { "arg": [1 2 3] }
        out_file = "fname"

        the bag server will then a temporary file for arg and fname, write
        the list [1 2 3] into the file for arg, call Virtuoso, then read
        the output file fname and return the result.

        Parameters
        ----------
        expr : string
            the skill expression to evaluate.
        input_files : dict[string, any] or None
            A dictionary of input files content.
        out_file : string or None
            the output file name argument in expr.

        Returns
        -------
        result : str
            a string representation of the result.

        Raises
        ------
        VirtuosoException :
            if virtuoso encounters errors while evaluating the expression.
        """
        request = dict(
            type='skill',
            expr=expr,
            input_files=input_files,
            out_file=out_file,
        )

        reply = self.send(request)
        return handle_reply(reply)

    def close(self):
        # type: () -> None
        DbAccess.close(self)
        if self._oa_db is not None:
            self._oa_db.close()
            self._oa_db = None

    def get_exit_object(self):
        # type: () -> Any
        return {'type': 'exit'}

    def get_cells_in_library(self, lib_name):
        # type: (str) -> List[str]
        return self._oa_db.get_cells_in_lib(lib_name)

    def create_library(self, lib_name, lib_path=''):
        # type: (str, str) -> None
        lib_path = lib_path or self.default_lib_path
        tech_lib = self.db_config['schematic']['tech_lib']
        self._oa_db.create_lib(lib_name, lib_path, tech_lib)

    def configure_testbench(self, tb_lib, tb_cell):
        # type: (str, str) -> Tuple[str, List[str], Dict[str, str], Dict[str, str]]
        raise NotImplementedError('Not implemented yet.')

    def get_testbench_info(self, tb_lib, tb_cell):
        # type: (str, str) -> Tuple[List[str], List[str], Dict[str, str], Dict[str, str]]
        raise NotImplementedError('Not implemented yet.')

    def update_testbench(self,  # type: DbAccess
                         lib,  # type: str
                         cell,  # type: str
                         parameters,  # type: Dict[str, str]
                         sim_envs,  # type: Sequence[str]
                         config_rules,  # type: Sequence[List[str]]
                         env_parameters,  # type: Sequence[List[Tuple[str, str]]]
                         ):
        # type: (...) -> None
        raise NotImplementedError('Not implemented yet.')

    def instantiate_schematic(self, lib_name, content_list, lib_path='',
                              sch_view='schematic', sym_view='symbol'):
        # type: (str, Sequence[Any], str, str, str) -> None

        # release write locks
        cell_view_list = []
        for cell_name, _ in content_list:
            cell_view_list.append((cell_name, sch_view))
            cell_view_list.append((cell_name, sym_view))
        self.release_write_locks(lib_name, cell_view_list)
        # create schematics
        self.create_library(lib_name, lib_path=lib_path)
        self._oa_db.implement_sch_list(lib_name, sch_view, sym_view, content_list)
        # refresh cell views
        self.refresh_cellviews(lib_name, cell_view_list)

    def instantiate_layout_pcell(self, lib_name, cell_name, view_name,
                                 inst_lib, inst_cell, params, pin_mapping):
        # type: (str, str, str, str, str, Dict[str, Any], Dict[str, str]) -> None
        raise NotImplementedError('Not implemented yet.')

    def instantiate_layout(self, lib_name, content_list, lib_path='', view='layout'):
        # type: (str, Sequence[Any], str, str) -> None
        cell_view_list = []
        for cell_name, _ in content_list:
            cell_view_list.append((cell_name, view))
        self.release_write_locks(lib_name, cell_view_list)
        # create layouts
        self.create_library(lib_name, lib_path=lib_path)
        self._oa_db.implement_lay_list(lib_name, view, content_list)
        # refresh cell views
        self.refresh_cellviews(lib_name, cell_view_list)

    def release_write_locks(self, lib_name, cell_view_list):
        # type: (str, Sequence[Tuple[str, str]]) -> None
        cmd = 'release_write_locks( "%s" {cell_view_list} )' % lib_name
        in_files = {'cell_view_list': cell_view_list}
        self._eval_skill(cmd, input_files=in_files)

    def refresh_cellviews(self, lib_name, cell_view_list):
        # type: (str, Sequence[Tuple[str, str]]) -> None
        cmd = 'refresh_cellviews( "%s" {cell_view_list} )' % lib_name
        in_files = {'cell_view_list': cell_view_list}
        self._eval_skill(cmd, input_files=in_files)

    def perform_checks_on_cell(self, lib_name, cell_name, view_name):
        # type: (str, str, str) -> None
        self._eval_skill(
            'check_and_save_cell( "{}" "{}" "{}" )'.format(lib_name, cell_name, view_name))

    def create_schematic_from_netlist(self, netlist, lib_name, cell_name,
                                      sch_view=None, **kwargs):
        # type: (str, str, str, Optional[str], Any) -> None
        # get netlists to copy
        netlist_dir = os.path.dirname(netlist)
        netlist_files = self.checker.get_rcx_netlists(lib_name, cell_name)
        if not netlist_files:
            # some error checking.  Shouldn't be needed but just in case
            raise ValueError('RCX did not generate any netlists')

        # copy netlists to a "netlist" subfolder in the CAD database
        cell_dir = self.get_cell_directory(lib_name, cell_name)
        targ_dir = os.path.join(cell_dir, 'netlist')
        os.makedirs(targ_dir, exist_ok=True)
        for fname in netlist_files:
            shutil.copy(os.path.join(netlist_dir, fname), targ_dir)

        # create symbolic link as aliases
        symlink = os.path.join(targ_dir, 'netlist')
        try:
            os.remove(symlink)
        except FileNotFoundError:
            pass
        os.symlink(netlist_files[0], symlink)

    def get_cell_directory(self, lib_name, cell_name):
        # type: (str, str) -> str
        """Returns the directory name of the given cell.

        Parameters
        ----------
        lib_name : str
            library name.
        cell_name : str
            cell name.

        Returns
        -------
        cell_dir : str
            path to the cell directory.
        """
        return os.path.join(self._oa_db.get_lib_path(lib_name), cell_name)

    def create_verilog_view(self, verilog_file, lib_name, cell_name, **kwargs):
        # type: (str, str, str, Any) -> None
        # delete old verilog view
        cmd = 'delete_cellview( "%s" "%s" "verilog" )' % (lib_name, cell_name)
        self._eval_skill(cmd)
        cmd = 'schInstallHDL("%s" "%s" "verilog" "%s" t)' % (lib_name, cell_name, verilog_file)
        self._eval_skill(cmd)

    def import_sch_cellview(self, lib_name, cell_name, view_name):
        # type: (str, str, str) -> None
        if lib_name not in self.lib_path_map:
            self.add_sch_library(lib_name)

        # read schematic information
        cell_list = self._oa_db.read_sch_recursive(lib_name, cell_name, view_name)

        # create python templates
        self._create_sch_templates(cell_list)

    def import_design_library(self, lib_name, view_name):
        # type: (str, str) -> None
        if lib_name not in self.lib_path_map:
            self.add_sch_library(lib_name)

        if lib_name == 'BAG_prim':
            # reading BAG primitives library, don't need to parse YAML files,
            # just get the cell list
            cell_list = [(lib_name, cell) for cell in self.get_cells_in_library(lib_name)]
        else:
            # read schematic information
            cell_list = self._oa_db.read_library(lib_name, view_name)

        # create python templates
        self._create_sch_templates(cell_list)

    def _create_sch_templates(self, cell_list):
        for lib, cell in cell_list:
            python_file = os.path.join(self.lib_path_map[lib], cell + '.py')
            if not os.path.exists(python_file):
                content = self.get_python_template(lib, cell,
                                                   self.db_config.get('prim_table', {}))
                bag.io.write_file(python_file, content + '\n', mkdir=False)
