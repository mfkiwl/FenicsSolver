# -*- coding: utf-8 -*-
# ***************************************************************************
# *                                                                         *
# *   Copyright (c) 2017 - Qingfeng Xia <qingfeng.xia iesensor.com>         *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************


from __future__ import print_function, division
import math
import numpy as np

from dolfin import *

supported_scaler_equations = {'temperature', 'electric_potential', 'species_concentration'}
# For small species factor, diffusivity = primary species, such as dye in water, always with convective velocity
# electric_potential, only for dieletric material electrostatics (permittivity/conductivity << 1)
# magnetic_potential is a vector, magnetostatics (static current) is solved in MaxwellEMSolver (permittivity/conductivity >> 1)
# porous pressure, e.g. underground water pressure 

# thermal diffusivity = (thermal conductivity) / (density * specific heat)
# thermal volumetric capacity = density * specific heat

from SolverBase import SolverBase, SolverError
class ScalerEquationSolver(SolverBase):
    """  this is a general scaler solver, modelled after Heat Transfer Equation, other solver can derive from this basic one
    # 4 types of boundaries supported:
    # body source unit:  W/m^3
    # convective velocity: m/s, 
    # Thermal Conductivity:  w/(K m)
    # Specific Heat Capacity, Cp:  J/(kg K)
    # thermal specific:
    # shear_heating: common in lubrication scinario, high viscosity and high shear speed, one kind of volume/body source
    # radiation: 
    """
    def __init__(self, s):
        SolverBase.__init__(self, s)

        if 'scaler_name' in self.settings:
            self.scaler_name = self.settings['scaler_name'].lower()
        else:
            self.scaler_name = "temperature"

        if self.scaler_name == "eletric_potential":
            assert self.settings['transient_settings']['transient'] == False

        if self.scaler_name == "temperature":
            if 'radiation_settings' in self.settings:
                self.radiation_settings = self.settings['radiation_settings']
                self.has_radiation = True
            else:
                self.has_radiation = False

        if 'convective_velocity' in self.settings and self.settings['convective_velocity']:
            self.convective_velocity = self.settings['convective_velocity']
        else:
            self.convective_velocity = None

    def capacity(self):
        # to calc diffusion coeff : conductivity/capacity, it must be number only
        if 'capacity' in self.material:
            c = self.material['capacity']
        # if not found, calc it
        if self.scaler_name == "temperature":
            c = self.material['density'] * self.material['specific_heat_capacity']
        elif self.scaler_name == "electric_potential":
            c = 1  # depends on source and boundary flux physical value
        elif self.scaler_name == "spicies_concentration":
            c = 1
        else:
            raise SolverError('material capacity property is not found for {}'.format(self.scaler_name))
        return self.get_material_value(c)  # to deal with nonlinear material

    def diffusivity(self):
        if 'diffusivity' in self.material:
            c = self.material['diffusivity']
        elif self.scaler_name == "temperature":
            c = self.material['thermal_conductivity'] / self.capacity()
        elif self.scaler_name == "electric_potential":
            c = self.material['electric_permittivity']
        elif self.scaler_name == "spicies_concentration":
            c = self.material['diffusivity']
        else:
            raise SolverError('conductivity material property is not found for {}'.format(self.scaler_name))
        return self.get_material_value(c)  # to deal with nonlinear material

    def conductivity(self):
        if 'conductivity' in self.material:
            c = self.material['conductivity']
        elif self.scaler_name == "temperature":
            c = self.material['thermal_conductivity']
        elif self.scaler_name == "electric_potential":
            c = self.material['electric_permittivity']
        elif self.scaler_name == "spicies_concentration":
            c = self.material['diffusivity']
        else:
            c = self.diffusivity() * self.capacity()
        return self.get_material_value(c)  # to deal with nonlinear material

    def get_convective_velocity_function(self, convective_velocity):
        self.vector_space = VectorFunctionSpace(self.mesh, 'CG', self.degree+1)
        vel = self.translate_value(convective_velocity, self.vector_space)
        #print('type of convective_velocity', type(convective_velocity), type(vel))
        #print("vel.ufl_shape", vel.ufl_shape)
        return vel

    def update_boundary_conditions(self, time_iter_, T, Tq, ds):
        k = self.conductivity() # constant, experssion or tensor
        bcs = []
        integrals_N = []  # heat flux
        for name, bc_settings in self.boundary_conditions.items():
            i = bc_settings['boundary_id']
            bc = self.get_boundary_variable(bc_settings)

            if bc['type'] == 'Dirichlet' or bc['type'] == 'fixedValue':
                T_bc = self.translate_value(bc['value'])
                dbc = DirichletBC(self.function_space, T_bc, self.boundary_facets, i)
                bcs.append(dbc)
            elif bc['type'] == 'Neumann' or bc['type'] =='fixedGradient':  # unit: K/m
                g = self.translate_value(bc['value'])
                integrals_N.append(k*g*Tq*ds(i))  # only work for constant conductivty k
                #integrals_N.append(inner(k * (normal*g), Tq)*ds(i))  # not working
            elif bc['type'].lower().find('flux')>=0 or bc['type'] == 'electric_current':
                # flux is a general flux, heatFlux: W/m2, it is not a general flux name
                g = self.translate_value(bc['value'])
                integrals_N.append(g*Tq*ds(i))
            elif bc['type'] == 'mixed' or bc['type'] == 'Robin':
                T_bc = self.translate_value(bc['value'])
                g = self.translate_value(bc['gradient'])
                integrals_N.append(k*g*Tq*ds(i))  # only work for constant conductivty k
                dbc = DirichletBC(self.function_space, T_bc, self.boundary_facets, i)
                bcs.append(dbc)
            elif bc['type'] == 'HTC':  # FIXME: HTC is not a general name or general type, only for thermal analysis
                #Robin, how to get the boundary value,  T as the first, HTC as the second
                Ta = self.translate_value(bc['ambient'])
                htc = self.translate_value(bc['value'])  # must be specified in Constant or Expressed in setup dict
                integrals_N.append( htc*(Ta-T)*Tq*ds(i))
            else:
                raise SolverError('boundary type`{}` is not supported'.format(bc['type']))
        return bcs, integrals_N

    def generate_form(self, time_iter_, T, Tq, T_current, T_prev):
        # T, Tq can be shared between time steps, form is unified diffussion coefficient
        normal = FacetNormal(self.mesh)

        dx= Measure("dx", subdomain_data=self.subdomains)  # 
        ds= Measure("ds", subdomain_data=self.boundary_facets)

        conductivity = self.conductivity() # constant, experssion or tensor
        capacity = self.capacity()  # density * specific capacity -> volumetrical capacity
        diffusivity = self.diffusivity()  # diffusivity

        bcs, integrals_N = self.update_boundary_conditions(time_iter_, T, Tq, ds)
        # boundary type is defined in FreeCAD FemConstraintFluidBoundary and its TaskPanel
        # zeroGradient is default thermal boundary, no effect on equation?

        def get_source_item():
            if isinstance(self.body_source, dict):
                S = []
                for k,v in self.get_body_source().items():
                    # it is good to using DG for multi-scale meshing, subdomain marking double
                    S.append(v['value']*Tq*dx(v['subdomain_id']))
                return sum(S)
            else:
                if self.body_source:
                    return self.get_body_source()*Tq*dx
                else:
                    return None
        #print("body_source: ", self.get_body_source())

        # poission equation, unified for all kind of variables
        def F_static(T, Tq):
            F =  inner( conductivity * grad(T), grad(Tq))*dx
            F -= sum(integrals_N)
            return F

        def F_convective():
            h = CellSize(self.mesh)  # cell size
            velocity = self.get_convective_velocity_function(self.convective_velocity)
            if self.transient_settings['transient']:
                dt = self.get_time_step(time_iter_)
                # Mid-point solution
                T_mid = 0.5*(T_prev + T)
                # Residual
                res = ((T - T_prev)/dt + dot(velocity, grad(T_mid)))*capacity - conductivity * div(grad(T_mid))  # does not support conductivity tensor
                # Galerkin variational problem
                F = ((T - T_prev)/dt+ dot(velocity, grad(T_mid)))*capacity*dx*Tq + conductivity * dot(grad(Tq), grad(T_mid))*dx
            else:
                T_mid = T
                # Residual
                res = dot(velocity, grad(T_mid))*capacity - conductivity * div(grad(T_mid))   # does not support tensor conductivity
                #print(res)
                # Galerkin variational problem
                F = Tq*dot(velocity, grad(T_mid))*capacity*dx + conductivity * dot(grad(Tq), grad(T_mid))*dx

            F -= sum(integrals_N)  # included in F_static()
            if self.body_source:
                print(self.body_source)
                res -= self.get_body_source()
                F -= get_source_item()
            # Add SUPG stabilisation terms
            vnorm = sqrt(dot(velocity, velocity))
            F += (h/(2.0*vnorm))*dot(velocity, grad(Tq))*res*dx
            return F

        if self.convective_velocity:  # convective heat conduction
            F = F_convective()
        else:
            if self.transient_settings['transient']:
                dt = self.get_time_step(time_iter_)
                theta = Constant(0.5) # Crank-Nicolson time scheme
                # Define time discretized equation, it depends on scaler type:  Energy, Species,
                F = (1.0/dt)*inner(T-T_prev, Tq)*capacity*dx \
                       + theta*F_static(T, Tq) + (1.0-theta)*F_static(T_prev, Tq)  # FIXME:  check using T_0 or T_prev ? 
            else:
                F = F_static(T, Tq)
            #print(F, get_source_item())
            if self.body_source:
                F -= get_source_item() 

        if self.scaler_name == "temperature" and self.has_radiation:
            Stefan_constant = 5.670367e-8  # W/m-2/K-4
            if 'emissivity' in self.material:
                emissivity = self.material['emissivity']  # self.settings['radiation_settings']['emissivity'] 
            else:
                emissivity = 1.0
            T_ambient_radiaton = self.radiation_settings['ambient_temperature']
            m_ = emissivity * Stefan_constant
            radiation_flux = m_*(T_ambient_radiaton**4 - pow(T, 4))  # it is nonlinear item
            print(m_, radiation_flux, F)
            F -= radiation_flux*Tq*ds # for all surface, without considering view angle
            F = action(F, T_current)  # API 1.0 still working ; newer API , replacing TrialFunction with Function for nonlinear 
            self.J = derivative(F, T_current, T)  # Gateaux derivative
        return F, bcs

    def solve_static(self, F, T_current, bcs):
        if self.scaler_name == "temperature" and self.has_radiation:
            return self.solve_nonlinear_problem(F, T_current, bcs, self.J)
        else:
            return self.solve_linear_problem(F, T_current, bcs)

    ############## public API ##########################

    def export(self):
        #save and return save file name, also timestamp
        result_filename = self.settings['case_folder'] + os.path.sep + "temperature" + "_time0" +  ".vtk"
        return result_filename

