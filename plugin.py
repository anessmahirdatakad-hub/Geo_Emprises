# -*- coding: utf-8 -*-

import os
import logging
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .geo_emprises import App


class GeoEmprisesPlugin:
    """Plugin QGIS pour Geo Emprises."""

    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dlg = None
        self.menu = "&Geo Emprises"

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.svg")
        self.action = QAction(QIcon(icon_path), "Geo Emprises", self.iface.mainWindow())
        self.action.setObjectName("geoEmprisesAction")
        self.action.setStatusTip("Générer des emprises et extraire les orthophotos concernées")
        self.action.triggered.connect(self.run)

        self.iface.addPluginToMenu(self.menu, self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.dlg is not None:
            try:
                self.dlg.close()
            except RuntimeError as exc:
                logging.debug("Fermeture du dialogue Geo Emprises impossible : %s", exc)
            self.dlg = None

        if self.action is not None:
            self.iface.removePluginMenu(self.menu, self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None

    def run(self):
        if self.dlg is None:
            self.dlg = App()
            self.dlg.setAttribute(Qt.WA_DeleteOnClose, False)

        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()
