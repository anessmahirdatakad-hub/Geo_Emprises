# -*- coding: utf-8 -*-

from .plugin import GeoEmprisesPlugin


def classFactory(iface):
    return GeoEmprisesPlugin(iface)
