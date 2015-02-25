# -*- coding: utf-8 -*-
# Copyright 2014-2015 Michael Helmling
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3 as
# published by the Free Software Foundation


"""
This module contains integer programming (IP) decoders for binary linear block codes,
based on the formulation called 'IPD' in:
Helmling, M., Ruzika, S. and Tanatmis, A.: "Mathematical Programming Decoding of Binary Linear
Codes: Theory and Algorithms", IEEE Transactions on Information Theory, Vol. 58 (7), 2012,
pp. 4753-4769.

"""
from __future__ import division, print_function
from collections import OrderedDict
import numpy as np
from lpdec.decoders import Decoder, cplexhelpers


class CplexIPDecoder(cplexhelpers.CplexDecoder):
    """CPLEX implementation of the IPD maximum-likelihood decoder.

    For ML simulations, the decoding process can be speed up using a shortcut callback.

    .. attribute:: z

       Vector of names of the auxiliary variables
    """
    def __init__(self, code, name=None, **kwargs):
        if name is None:
            name = 'CplexIPDecoder'
        cplexhelpers.CplexDecoder.__init__(self, code, name, **kwargs)
        matrix = code.parityCheckMatrix
        self.z = ['z' + str(num) for num in range(matrix.shape[0])]
        self.cplex.variables.add(types=['I'] * matrix.shape[0], names=self.z)
        self.cplex.linear_constraints.add(
            names=['parity_check_' + str(num) for num in range(matrix.shape[0])])
        for cnt, row in enumerate(matrix):
            nonzero_indices = [(self.x[i], row[i]) for i in range(row.size) if row[i]]
            nonzero_indices.append((self.z[cnt], -2))
            self.cplex.linear_constraints.set_linear_components(
                'parity_check_{0}'.format(cnt),
                zip(*nonzero_indices))

    def minimumDistance(self, hint=None):
        """Calculate the minimum distance of :attr:`code` via integer programming.

        Compared to the decoding formulation, this adds the constraint :math:`|x| \\geq 1` and
        minimizes :math:`\\sum_{i=1}^n x`.
        """
        self.cplex.linear_constraints.add(
            names=['nonzero'], lin_expr=[(self.x, np.ones(len(self.x)))], senses='G', rhs=[1])
        self.cplex.parameters.mip.tolerances.absmipgap.set(1-1e-5)  # all solutions are integral
        self.setLLRs(np.ones(self.code.blocklength))
        self.solve(hint)
        self.cplex.linear_constraints.delete('nonzero')
        return int(round(self.objectiveValue))

    def fix(self, index, value):
        if value == 0:
            self.cplex.variables.set_upper_bounds(self.x[index], 0)
        else:
            self.cplex.variables.set_lower_bounds(self.x[index], 1)

    def release(self, index):
        self.cplex.variables.set_lower_bounds(self.x[index], 0)
        self.cplex.variables.set_upper_bounds(self.x[index], 1)

    def params(self):
        params = OrderedDict(name=self.name)
        params['cplexParams'] = self.cplexParams(self.cplex)
        return params


class GurobiIPDecoder(Decoder):
    """Gurobi implementation of the IPD maximum-likelihood decoder.

    :param LinearBlockCode code: The code to decoder.
    :param dict gurobiParams: Optional dictionary of parameters; these are passed to the Gurobi
        model via :func:`gurobipy.Model.setParam`. The attributes :attr:`tuningSet1`,
        :attr:`tuningSet2` and :attr:`tuningSet3` contain three sets of parameters that were
        obtained from the Gurobi tuning tool.
    :param str gurobiVersion: Version of the Gurobi package; if supplied, an error is raised if
        the current version does not match.
    :param str name: Name of the decoder. Defaults to "GurobiIPDecoder".

    The number of nodes in Gurobi's branch-and-bound procedure is collected in the statistics.

    Example usage:
        >>> from lpdec.imports import *
        >>> code = HammingCode(3)
        >>> decoder = GurobiIPDecoder(code, gurobiParams=GurobiIPDecoder.tuningSet1, name='GurobiTuned')
        >>> result = decoder.decode([1, -1, 0, -1.5, 2, 3, 0])
        >>> print(result)

    .. attribute:: tuningSet1

    Dictionary to be passed to the constructor as *gurobiParams*; this set of parameters was
    obtained from the Gurobi tuning tool on a hard instance for the (155,93) Tanner LDPC code.

    .. attribute:: tuningSet2

    As above; second-best parameter set.

    .. attribute:: tuningSet3

    As above; third-best parameter set.
    """
    def __init__(self, code, gurobiParams=None, gurobiVersion=None, name=None):

        if name is None:
            name = 'GurobiIPDecoder'
        Decoder.__init__(self, code, name)
        from gurobipy import Model, GRB, quicksum, gurobi
        matrix = code.parityCheckMatrix
        self.model = Model('lpdec ML Decoder')
        self.model.setParam('OutputFlag', 0)
        if gurobiParams is None:
            gurobiParams = dict()
        for param, value in gurobiParams.items():
            self.model.setParam(param, value)
        self.grbParams = gurobiParams
        if gurobiVersion:
            installedVersion = '.'.join(str(v) for v in gurobi.version())
            if gurobiVersion != installedVersion:
                raise RuntimeError('Installed Gurobi version {} does not match requested {}'
                                   .format(installedVersion, gurobiVersion))
        if code.q == 2:
            self.x = [self.model.addVar(vtype=GRB.BINARY, name="x{}".format(i))
                      for i in range(code.blocklength)]
        else:
            self.x = OrderedDict()
            for i in range(code.blocklength):
                for k in range(1, code.q):
                    self.x[i, k] = self.model.addVar(vtype=GRB.BINARY, name='x{},{}'.format(i, k))

        self.model.update()
        for i in range(code.blocklength):
            self.model.addConstr(quicksum(self.x[i, k] for k in range(1, code.q)),
                                 GRB.LESS_EQUAL, 1)
        self.z = []
        for i in range(matrix.shape[0]):
            ub = np.sum(matrix[i]) * (code.q - 1) // 3
            self.z.append(self.model.addVar(0, ub, vtype=GRB.INTEGER, name='z{}'.format(i)))
        self.model.update()
        for z, row in zip(self.z, matrix):
            if code.q == 2:
                self.model.addConstr(quicksum(self.x[i] for i in np.flatnonzero(row)) - 2 * z,
                                     GRB.EQUAL, 0)
            else:
                self.model.addConstr(quicksum(row[i]*k*self.x[i, k] for k in range(1, code.q) for i
                                              in np.flatnonzero(row)) - code.q * z, GRB.EQUAL, 0)
        self.model.update()
        self.mlCertificate = self.foundCodeword = True

    tuningSet1 = dict(MIPFocus=2, PrePasses=2, Presolve=2)
    tuningSet2 = dict(MIPFocus=2, VarBranch=1)
    tuningSet3 = dict(MIPFocus=2)

    def setStats(self, stats):
        if 'nodes' not in stats:
            stats['nodes'] = 0
        Decoder.setStats(self, stats)

    def setLLRs(self, llrs, sent=None):
        from gurobipy import GRB, LinExpr
        self.model.setObjective(LinExpr(llrs, self.x if self.code.q == 2 else self.x.values()))
        Decoder.setLLRs(self, llrs, sent)

    @staticmethod
    def callback(model, where):
        """ A callback function for Gurobi that is able to terminate the MIP solver if a solution
        which is better than the sent codeword has been found.
        """
        from gurobipy import GRB
        if where == GRB.Callback.MIPNODE:
            if model.cbGet(GRB.Callback.MIPNODE_OBJBST) < model._realObjective - 1e-6:
                model._incObj = model.cbGet(GRB.Callback.MIPNODE_OBJBST)
                model.terminate()

    def solve(self, lb=-np.inf, ub=np.inf):
        q = self.code.q
        from gurobipy import GRB
        self.mlCertificate = True
        if self.sent is not None:
            sent = np.asarray(self.sent)
            if q == 2:
                for val, var in zip(sent, self.x):
                    var.Start = val
                self.model._realObjective = np.dot(self.sent, self.llrs)
            else:
                self.model._realObjective = 0
                for i, val in enumerate(sent):
                    for k in range(1, q):
                        self.x[i, k].Start = 1 if val == k else 0
                    if val != 0:
                        self.model._realObjective += self.llrs[i*(q-1)+val-1]
            zValues = np.dot(self.code.parityCheckMatrix, sent // q).tolist()
            for val, var in zip(zValues, self.z):
                var.Start = val
            self.model._incObj = None
            self.model.optimize(GurobiIPDecoder.callback)
        else:
            self.model.optimize()
        if self.model.getAttr('Status') == GRB.INTERRUPTED:
            if self.sent is None or self.model._incObj is None:
                raise KeyboardInterrupt()
            else:
                self.objectiveValue = self.model._incObj
                self.mlCertificate = False
        self._stats["nodes"] += self.model.getAttr("NodeCount")
        self.objectiveValue = self.model.objVal
        if q == 2:
            for i, x in enumerate(self.x):
                self.solution[i] = x.x
        else:
            for i in range(self.code.blocklength):
                self.solution[i] = 0
                for k in range(1, q):
                    if self.x[i, k].X > .5:
                        self.solution[i] = k

    def minimumDistance(self, hint=None):
        """Calculate the minimum distance of :attr:`code` via integer programming.

        Compared to the decoding formulation, this adds the constraint :math:`|x| \\geq 1` and
        minimizes :math:`\\sum_{i=1}^n x`.
        """
        from gurobipy import quicksum, GRB
        assert self.code.q == 2
        self.model.addConstr(quicksum(self.x), GRB.GREATER_EQUAL, 1, name='excludeZero')
        self.model.setParam('MIPGapAbs', 1-1e-5)
        self.setLLRs(np.ones(self.code.blocklength))
        self.solve()
        self.model.remove(self.model.getConstrByName('excludeZero'))
        self.model.update()
        return int(round(self.objectiveValue))

    def params(self):
        ret = OrderedDict()
        if len(self.grbParams):
            ret['gurobiParams'] = self.grbParams
        import gurobipy
        ret['gurobiVersion'] = '.'.join(str(v) for v in gurobipy.gurobi.version())
        ret['name'] = self.name
        return ret