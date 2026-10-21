# -*- coding: utf-8 -*-
import logging
"""
Geo Emprises
- Génération des emprises des fichiers LAZ / JP2 / TIF / TIFF
- Extraction d'orthophotos à partir d'emprises existantes + plans SHP/DXF de toute géométrie

Pré-requis :
    QGIS LTR installé
    Python de QGIS / environnement contenant PyQt5, qgis, osgeo

IMPORTANT :
    Le module d'extraction NE recrée PAS les emprises.
    Il réutilise directement le Shapefile d'emprises fourni par l'utilisateur.
"""

import os
import sys
import subprocess
import tempfile
import smtplib
from urllib.parse import quote
from pathlib import Path

# =========================================================
# RESSOURCES
# =========================================================

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

def resource_path(relative_path):
    return os.path.join(PLUGIN_DIR, relative_path)


LOGO_PATH = resource_path("logo.png")
ICON_PATH = resource_path("logo.ico")


# =========================================================
# ENVIRONNEMENT QGIS
# =========================================================

# Dans un plugin QGIS, QGIS et son environnement sont déjà initialisés.

# =========================================================
# QT
# =========================================================

from qgis.PyQt.QtWidgets import (
    QApplication,
    QWidget,
    QLabel,
    QPushButton,
    QFileDialog,
    QLineEdit,
    QTextEdit,
    QVBoxLayout,
    QHBoxLayout,
    QMessageBox,
    QComboBox,
    QProgressBar,
    QTabWidget,
    QListWidget,
    QListWidgetItem,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QScrollArea,
    QFrame,
    QFormLayout,
    QSizePolicy,
)

from qgis.PyQt.QtCore import (
    Qt,
    QVariant,
    QThread,
    pyqtSignal,
    QSettings,
    QUrl,
)

from qgis.PyQt.QtGui import (
    QPixmap,
    QIcon,
    QDesktopServices,
)


# =========================================================
# COMPATIBILITÉ QT5 / QT6 (QGIS 3.x / QGIS 4.x)
# =========================================================

# QGIS 3.x utilise Qt5/PyQt5 et QGIS 4.x utilise Qt6/PyQt6.
# Le namespace qgis.PyQt permet de conserver un même code source,
# mais certains enums et QDialog.exec_() ont changé de forme.
try:
    QT_ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter
except AttributeError:
    QT_ALIGN_CENTER = Qt.AlignCenter

try:
    QT_KEEP_ASPECT_RATIO = Qt.AspectRatioMode.KeepAspectRatio
except AttributeError:
    QT_KEEP_ASPECT_RATIO = Qt.KeepAspectRatio

try:
    QT_SMOOTH_TRANSFORMATION = Qt.TransformationMode.SmoothTransformation
except AttributeError:
    QT_SMOOTH_TRANSFORMATION = Qt.SmoothTransformation

try:
    QSP_EXPANDING = QSizePolicy.Policy.Expanding
except AttributeError:
    QSP_EXPANDING = QSizePolicy.Expanding

try:
    QSA_NO_FRAME = QFrame.Shape.NoFrame
except AttributeError:
    QSA_NO_FRAME = QFrame.NoFrame

try:
    QLE_PASSWORD = QLineEdit.EchoMode.Password
except AttributeError:
    QLE_PASSWORD = QLineEdit.Password

try:
    QDBB_OK = QDialogButtonBox.StandardButton.Ok
    QDBB_CANCEL = QDialogButtonBox.StandardButton.Cancel
    QDBB_CLOSE = QDialogButtonBox.StandardButton.Close
except AttributeError:
    QDBB_OK = QDialogButtonBox.Ok
    QDBB_CANCEL = QDialogButtonBox.Cancel
    QDBB_CLOSE = QDialogButtonBox.Close

try:
    QDIALOG_ACCEPTED = QDialog.DialogCode.Accepted
except AttributeError:
    QDIALOG_ACCEPTED = QDialog.Accepted


def dialog_exec(dialog):
    """Exécute un QDialog sous PyQt5 ou PyQt6."""
    exec_method = getattr(dialog, "exec", None)
    if callable(exec_method):
        return exec_method()
    return dialog.exec_()


# =========================================================
# QGIS
# =========================================================

from qgis.core import (
    QgsApplication,
    QgsVectorLayer,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsRectangle,
    QgsVectorFileWriter,
    QgsPointCloudLayer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsWkbTypes,
    QgsProject,
)

from qgis.gui import QgsProjectionSelectionWidget

from osgeo import gdal, ogr


# =========================================================
# INITIALISATION QGIS
# =========================================================

# Déjà assurée par QGIS lorsqu'il charge le plugin.

# =========================================================
# OUTILS
# =========================================================

RASTER_EXTENSIONS = {
    "JP2": "*.jp2",
    "TIF": "*.tif",
    "TIFF": "*.tiff",
    "LAZ": "*.laz",
}


def find_raster_file(root_folder, filename, parent_folder=None):
    """
    Recherche le raster correspondant au champ Nom de l'emprise.

    Priorité :
    1. dossier racine + parent_folder + filename
    2. recherche récursive exacte par nom
    """
    root = Path(root_folder)

    if not root.exists():
        return None

    if parent_folder:
        candidate = root / parent_folder / filename
        if candidate.exists():
            return str(candidate)

    # Recherche récursive exacte
    try:
        for p in root.rglob(filename):
            if p.is_file():
                return str(p)
    except OSError as exc:
        logging.debug("Recherche récursive impossible dans %s : %s", root, exc)

    return None


def unique_output_path(folder, filename):
    """
    Évite d'écraser un résultat existant.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    path = folder / filename

    if not path.exists():
        return str(path)

    stem = path.stem
    suffix = path.suffix

    i = 2
    while True:
        candidate = folder / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return str(candidate)
        i += 1


def save_geometry_layer(geometry, crs, path):
    """
    Crée un GeoPackage contenant la géométrie utilisée comme masque GDAL.
    """
    layer = QgsVectorLayer(
        f"Polygon?crs={crs.authid()}",
        "mask",
        "memory"
    )

    if not layer.isValid():
        raise Exception("Impossible de créer la couche masque.")

    provider = layer.dataProvider()

    feat = QgsFeature()
    feat.setGeometry(geometry)
    provider.addFeature(feat)
    layer.updateExtents()

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.fileEncoding = "UTF-8"
    options.layerName = "mask"

    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer,
        path,
        QgsProject.instance().transformContext(),
        options
    )

    if result[0] != QgsVectorFileWriter.NoError:
        raise Exception(
            f"Impossible d'écrire le masque : {result}"
        )


def export_vector_layer_compat(layer, out_path):
    """Exporte une couche vectorielle avec l'API disponible dans QGIS 3/4."""
    # API moderne disponible dans QGIS 3.10+ et QGIS 4.x.
    if hasattr(QgsVectorFileWriter, "writeAsVectorFormatV3"):
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "ESRI Shapefile"
        options.fileEncoding = "UTF-8"
        result = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer,
            str(out_path),
            QgsProject.instance().transformContext(),
            options
        )
        if isinstance(result, tuple):
            code = result[0]
        else:
            code = result
        if code != QgsVectorFileWriter.NoError:
            raise Exception(f"Erreur d'export Shapefile : {result}")
        return

    # Repli pour d'anciennes versions QGIS 3.
    result = QgsVectorFileWriter.writeAsVectorFormat(
        layer, str(out_path), "UTF-8", layer.crs(), "ESRI Shapefile"
    )
    if isinstance(result, tuple):
        code = result[0]
    else:
        code = result
    if code != QgsVectorFileWriter.NoError:
        raise Exception(f"Erreur d'export Shapefile : {result}")


# =========================================================
# WORKER : GENERATION DES EMPRISES
# =========================================================

class Worker(QThread):

    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)

    def __init__(
        self,
        folder,
        out,
        name,
        year,
        dtype
    ):
        super().__init__()

        self.folder = folder
        self.out = out
        self.name = name
        self.year = year
        self.dtype = dtype

    def log(self, msg):
        self.log_signal.emit(msg)

    def run(self):

        folder = Path(self.folder)

        layer = QgsVectorLayer(
            "Polygon?crs=EPSG:2154",
            "emprises",
            "memory"
        )

        pr = layer.dataProvider()

        pr.addAttributes([
            QgsField("Nom", QVariant.String),
            QgsField("Dossier", QVariant.String),
            QgsField("Annee", QVariant.String),
            QgsField("Type", QVariant.String),
        ])

        layer.updateFields()

        # =================================================
        # FILES
        # =================================================

        if self.dtype == "LAZ":
            files = list(folder.rglob("*.laz"))

        elif self.dtype == "JP2":
            files = list(folder.rglob("*.jp2"))

        elif self.dtype == "TIF":
            files = list(folder.rglob("*.tif"))

        else:
            files = list(folder.rglob("*.tiff"))

        total = len(files)

        if total == 0:
            self.log("Aucun fichier trouvé")
            return

        ok = 0
        err = 0

        # =================================================
        # LOOP
        # =================================================

        for i, f in enumerate(files):

            percent = int((i + 1) / total * 100)
            self.progress_signal.emit(percent)

            try:

                self.log(f"Traitement : {f.name}")

                # =========================================
                # LAZ
                # =========================================

                if self.dtype == "LAZ":

                    pc = QgsPointCloudLayer(
                        str(f),
                        f.stem,
                        "pdal"
                    )

                    if not pc.isValid():
                        raise Exception("LAZ invalide")

                    ext = pc.extent()

                # =========================================
                # RASTER
                # =========================================

                else:

                    ds = gdal.Open(str(f))

                    if not ds:
                        raise Exception("Raster invalide")

                    gt = ds.GetGeoTransform()

                    xmin = gt[0]
                    ymax = gt[3]

                    xmax = (
                        gt[0]
                        + gt[1] * ds.RasterXSize
                    )

                    ymin = (
                        gt[3]
                        + gt[5] * ds.RasterYSize
                    )

                    ext = QgsRectangle(
                        xmin,
                        ymin,
                        xmax,
                        ymax
                    )

                    ds = None

                # =========================================
                # GEOMETRY
                # =========================================

                geom = QgsGeometry.fromRect(ext)

                feat = QgsFeature()

                feat.setGeometry(geom)

                feat.setAttributes([
                    f.name,
                    f.parent.name,
                    self.year,
                    self.dtype
                ])

                pr.addFeature(feat)

                ok += 1

            except Exception as e:

                err += 1
                self.log(str(e))

        # =================================================
        # EXPORT
        # =================================================

        out = str(
            Path(self.out) / f"{self.name}.shp"
        )

        export_vector_layer_compat(layer, out)

        self.log("================================")
        self.log(f"OK : {ok}")
        self.log(f"ERREURS : {err}")
        self.log(f"SORTIE : {out}")


# =========================================================
# WORKER : COPIE DES ORTHOPHOTOS CONCERNEES
# =========================================================

class ExtractionWorker(QThread):
    """Copie les orthophotos touchées par des objets SHP ou DXF.

    Le CRS est lu depuis les métadonnées QGIS/OGR ou le fichier .prj/.qpj
    associé. Les géométries sont transformées uniquement en mémoire vers
    le CRS des emprises. Les fichiers sources ne sont jamais modifiés.
    """

    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    finished_signal = pyqtSignal()

    def __init__(self, footprint_path, plan_paths, raster_root, output_folder, plan_crs_overrides=None, dxf_global_extent=False, dxf_margin=0.0, dxf_mode="zone"):
        super().__init__()
        self.footprint_path = footprint_path
        self.plan_paths = plan_paths
        self.raster_root = raster_root
        self.output_folder = output_folder
        self.plan_crs_overrides = plan_crs_overrides or {}
        self.dxf_global_extent = bool(dxf_global_extent)
        self.dxf_margin = float(dxf_margin or 0.0)
        self.dxf_mode = str(dxf_mode or ("zone" if dxf_global_extent else "precis"))

    def log(self, msg):
        self.log_signal.emit(msg)

    def run(self):
        try:
            self.process()
        except Exception as e:
            self.log(f"ERREUR GENERALE : {e}")
        finally:
            self.finished_signal.emit()

    def _read_prj_crs(self, vector_path):
        """Lit le CRS d'un Shapefile de façon robuste depuis son .prj/.qpj.

        Certains anciens .prj utilisent du WKT ESRI ou un encodage différent.
        Plusieurs méthodes de lecture sont essayées. Aucune transformation
        de coordonnées n'est effectuée ici.
        """
        base = Path(vector_path)
        for prj in (base.with_suffix('.prj'), base.with_suffix('.qpj')):
            if not prj.exists():
                continue
            try:
                raw = prj.read_bytes()
                text = raw.decode('utf-8-sig', errors='ignore').strip()
                if not text:
                    text = raw.decode('cp1252', errors='ignore').strip()

                crs = QgsCoordinateReferenceSystem()
                if text and crs.createFromWkt(text) and crs.isValid():
                    return crs

                try:
                    crs2 = QgsCoordinateReferenceSystem.createFromUserInput(text)
                    if crs2.isValid():
                        return crs2
                except (TypeError, ValueError, RuntimeError) as exc:
                    logging.debug("Interprétation du CRS par QGIS impossible pour %s : %s", prj, exc)

                try:
                    from osgeo import osr
                    srs = osr.SpatialReference()
                    if hasattr(srs, 'SetAxisMappingStrategy'):
                        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
                    if srs.ImportFromWkt(text) == 0:
                        wkt = srs.ExportToWkt()
                        crs3 = QgsCoordinateReferenceSystem()
                        if crs3.createFromWkt(wkt) and crs3.isValid():
                            return crs3
                except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as exc:
                    logging.debug("Interprétation du CRS par OGR impossible pour %s : %s", prj, exc)
            except OSError as exc:
                logging.debug("Lecture du fichier de projection impossible %s : %s", prj, exc)
                continue
        return QgsCoordinateReferenceSystem()

    def _load_plan_layer(self, path):
        ext = Path(path).suffix.lower()
        if ext not in ('.shp', '.dxf'):
            raise Exception('Formats acceptés : SHP et DXF.')
        layer = QgsVectorLayer(path, Path(path).stem, 'ogr')
        if not layer.isValid():
            raise Exception(f'Impossible de lire le fichier vectoriel : {path}')
        return layer

    def _read_plan_source_crs(self, path, layer):
        """Détecte le CRS d'un SHP/DXF, avec possibilité de CRS fourni manuellement."""
        override = self.plan_crs_overrides.get(str(Path(path).resolve()))
        if override is not None and override.isValid():
            return override
        layer_crs = layer.crs()
        if layer_crs.isValid():
            return layer_crs
        return self._read_prj_crs(path)

    def _read_plan_geometries(self, path, target_crs):
        """Lit les géométries d'un SHP/DXF et les transforme en mémoire vers target_crs."""
        layer = self._load_plan_layer(path)

        source_crs = self._read_plan_source_crs(path, layer)
        if not source_crs.isValid():
            raise Exception(
                f"CRS absent ou invalide pour {Path(path).name}. "
                "Aucun CRS automatique ou manuel n'a été fourni."
            )

        # Vérification QGIS complémentaire : le CRS lu par la couche doit être cohérent.
        layer_crs = layer.crs()
        if layer_crs.isValid() and layer_crs != source_crs:
            self.log(
                f"AVERTISSEMENT CRS : {Path(path).name} | "
                f".prj={source_crs.authid() or source_crs.description()} | "
                f"QGIS={layer_crs.authid() or layer_crs.description()}"
            )

        source_id = source_crs.authid() or source_crs.description()
        target_id = target_crs.authid() or target_crs.description()
        source_origin = "fourni manuellement" if str(Path(path).resolve()) in self.plan_crs_overrides else "détecté automatiquement"
        self.log(f"CRS : {Path(path).name} -> {source_id} ({source_origin})")

        # Les plans peuvent avoir un CRS différent des emprises.
        # On transforme UNIQUEMENT les géométries en mémoire vers le CRS des emprises.
        # Le SHP, son .prj/.qpj et toutes ses coordonnées sources restent inchangés.
        needs_transform = (source_crs != target_crs)
        transformer = None
        if needs_transform:
            try:
                transformer = QgsCoordinateTransform(
                    source_crs, target_crs, QgsProject.instance()
                )
                self.log(
                    f"TRANSFORMATION CRS : {source_id} -> {target_id} "
                    "(uniquement en mémoire, fichier source inchangé)"
                )
            except Exception as e:
                raise Exception(
                    f"Impossible de préparer la transformation CRS {source_id} -> {target_id} : {e}"
                )
        else:
            self.log(f"CRS identique : aucune transformation nécessaire ({source_id})")

        geometries = []
        feature_count = 0
        type_counts = {}

        for feat in layer.getFeatures():
            geom = feat.geometry()
            if not geom or geom.isEmpty():
                continue

            feature_count += 1
            geom = QgsGeometry(geom)

            if transformer is not None:
                try:
                    # Transformation de la copie uniquement : le SHP source n'est jamais modifié.
                    result = geom.transform(transformer)
                    if result != 0:
                        raise Exception(f"code retour {result}")
                except Exception as e:
                    raise Exception(
                        f"Échec de transformation de la géométrie (objet {feature_count}) "
                        f"{source_id} -> {target_id} : {e}"
                    )

            geom_type = QgsWkbTypes.displayString(geom.wkbType())
            type_counts[geom_type] = type_counts.get(geom_type, 0) + 1
            geometries.append(geom)

        if not geometries:
            raise Exception(f"Aucune géométrie exploitable dans {Path(path).name}.")

        types_txt = ", ".join(f"{k}={v}" for k, v in sorted(type_counts.items()))
        self.log(
            f"OK : {Path(path).name} | objets={feature_count} | types={types_txt}"
        )
        return geometries

    def _build_raster_index(self):
        """Index nom -> chemin, construit une seule fois."""
        root = Path(self.raster_root)
        index = {}
        if not root.exists():
            return index

        allowed = {'.jp2', '.j2k', '.tif', '.tiff'}
        for p in root.rglob('*'):
            if p.is_file() and p.suffix.lower() in allowed:
                index.setdefault(p.name.lower(), str(p))
        return index

    def _find_raster(self, raster_index, name, dossier):
        name = str(name or '').strip()
        dossier = str(dossier or '').strip()
        if not name:
            return None

        if dossier:
            candidate = Path(self.raster_root) / dossier / name
            if candidate.is_file():
                return str(candidate)

            normalized_dossier = dossier.replace('\\', '/').strip('/').lower()
            for p in raster_index.values():
                pp = Path(p)
                try:
                    rel_parent = str(pp.parent.relative_to(Path(self.raster_root))).replace('\\', '/').lower()
                except ValueError:
                    continue
                if pp.name.lower() == name.lower() and (
                    rel_parent == normalized_dossier or rel_parent.endswith('/' + normalized_dossier)
                ):
                    return str(pp)

        return raster_index.get(name.lower())

    def _copy_once(self, raster_path, output_folder, copied):
        import shutil
        src = Path(raster_path)
        key = str(src.resolve()).lower()
        if key in copied:
            return False, copied[key]

        Path(output_folder).mkdir(parents=True, exist_ok=True)
        dst = Path(output_folder) / src.name
        if dst.exists():
            try:
                if dst.stat().st_size == src.stat().st_size:
                    copied[key] = str(dst)
                    return False, str(dst)
            except OSError as exc:
                logging.debug("Impossible de comparer les fichiers %s et %s : %s", src, dst, exc)
            dst = Path(unique_output_path(output_folder, src.name))

        shutil.copy2(str(src), str(dst))
        copied[key] = str(dst)
        return True, str(dst)

    def _dxf_global_geometry(self, geometries):
        """Construit une emprise globale rectangulaire autour de tous les objets DXF.

        Les DXF de plans peuvent contenir beaucoup de lignes/points qui ne
        représentent pas directement une surface. Pour l'extraction, on utilise
        donc leur emprise spatiale globale afin de récupérer toutes les emprises
        d'orthophotos situées dans la zone du plan. La géométrie est déjà dans
        le CRS des emprises.
        """
        if not geometries:
            return None

        combined = QgsRectangle()
        initialized = False
        for geom in geometries:
            if not geom or geom.isEmpty():
                continue
            rect = geom.boundingBox()
            if rect.isEmpty():
                continue
            if not initialized:
                combined = QgsRectangle(rect)
                initialized = True
            else:
                combined.combineExtentWith(rect)

        if not initialized or combined.isEmpty():
            return None

        margin = max(0.0, self.dxf_margin)
        if margin > 0:
            combined.setXMinimum(combined.xMinimum() - margin)
            combined.setYMinimum(combined.yMinimum() - margin)
            combined.setXMaximum(combined.xMaximum() + margin)
            combined.setYMaximum(combined.yMaximum() + margin)

        return QgsGeometry.fromRect(combined)

    def process(self):
        self.log("================================")
        self.log("DÉMARRAGE DE L'EXTRACTION")
        self.log("================================")

        footprints = QgsVectorLayer(self.footprint_path, "emprises_existantes", "ogr")
        if not footprints.isValid():
            raise Exception("Impossible de lire le fichier des emprises.")

        target_crs = footprints.crs()
        if not target_crs.isValid() and Path(self.footprint_path).suffix.lower() == '.shp':
            self.log("CRS des emprises non reconnu par OGR : lecture directe du .prj...")
            target_crs = self._read_prj_crs(self.footprint_path)
        if not target_crs.isValid():
            raise Exception(
                "Le CRS du fichier des emprises est inconnu ou le .prj est illisible. "
                "Vérifiez que le .prj accompagne bien le .shp et contient un WKT valide."
            )

        target_id = target_crs.authid() or target_crs.description()
        self.log(f"CRS des emprises : {target_id}")
        self.log("RÈGLE CRS : transformation automatique des plans vers le CRS des emprises, uniquement en mémoire. Les fichiers sources ne sont jamais modifiés.")

        fields = footprints.fields().names()
        for field in ("Nom", "Dossier"):
            if field not in fields:
                raise Exception(f"Champ obligatoire absent du Shapefile : {field}")

        # Index spatial des emprises existantes.
        from qgis.core import QgsSpatialIndex
        spatial_index = QgsSpatialIndex()
        footprint_by_id = {}
        valid_footprints = 0
        for feat in footprints.getFeatures():
            geom = feat.geometry()
            if not geom or geom.isEmpty():
                continue
            spatial_index.addFeature(feat)
            footprint_by_id[feat.id()] = feat
            valid_footprints += 1

        if not footprint_by_id:
            raise Exception("Aucune emprise géométrique valide.")
        self.log(f"Emprises indexées : {valid_footprints}")

        # Index des orthophotos.
        self.log("INDEXATION DES ORTHOPHOTOS...")
        raster_index = self._build_raster_index()
        self.log(f"Orthophotos indexées : {len(raster_index)}")
        if not raster_index:
            raise Exception("Aucune orthophoto JP2/TIF/TIFF trouvée dans le dossier.")

        # Lecture des plans : aucun filtrage par type géométrique.
        plans = []
        for path in self.plan_paths:
            try:
                self.log(f"Lecture : {Path(path).name}")
                geometries = self._read_plan_geometries(path, target_crs)
                plans.append((path, geometries))
            except Exception as e:
                self.log(f"PLAN IGNORÉ : {Path(path).name} -> {e}")

        if not plans:
            raise Exception("Aucun plan exploitable. Vérifiez les CRS et les géométries des plans.")

        total_geometries = sum(len(g) for _, g in plans)
        processed_geometries = 0
        total_plans = len(plans)
        self.log(
            f"MODE DXF : {self.dxf_mode}"
            + (f" | marge={self.dxf_margin:g} m" if self.dxf_global_extent else "")
        )
        copied = {}
        copied_count = 0
        candidate_count = 0
        missing_count = 0
        errors = 0
        touched_ids = set()

        for plan_no, (plan_path, geometries) in enumerate(plans, 1):
            is_dxf = Path(plan_path).suffix.lower() == '.dxf'
            self.log("--------------------------------")
            self.log(f"PLAN {plan_no}/{total_plans} : {Path(plan_path).name}")

            # ---------------------------------------------------------
            # DXF : mode emprise globale
            # ---------------------------------------------------------
            if is_dxf and self.dxf_global_extent and self.dxf_mode == "zone":
                global_geom = self._dxf_global_geometry(geometries)
                if global_geom is None or global_geom.isEmpty():
                    self.log(f"DXF IGNORÉ : emprise globale vide pour {Path(plan_path).name}")
                    continue

                self.log(
                    f"MODE DXF GLOBAL : {len(geometries)} objets regroupés en une emprise "
                    f"globale | marge={self.dxf_margin:g} m"
                )
                self.log(
                    f"Emprise DXF : {global_geom.boundingBox().toString()}"
                )

                bbox = global_geom.boundingBox()
                candidate_ids = spatial_index.intersects(bbox)
                candidate_count += len(candidate_ids)
                processed_geometries += len(geometries)

                local_touched = 0
                for fid in candidate_ids:
                    feat = footprint_by_id.get(fid)
                    if feat is None:
                        continue
                    # IMPORTANT : on utilise la zone globale du DXF comme
                    # zone de traitement, mais on vérifie quand même l'intersection
                    # géométrique réelle avec cette zone. Ainsi une ortho située
                    # dans la zone du plan est retenue, mais une ortho totalement
                    # à l'extérieur n'est jamais copiée.
                    try:
                        footprint_geom = feat.geometry()
                        if not footprint_geom or footprint_geom.isEmpty():
                            continue
                        if footprint_geom.disjoint(global_geom):
                            continue
                    except Exception as e:
                        errors += 1
                        self.log(f"ERREUR TEST ZONE DXF : {e}")
                        continue

                    if fid in touched_ids:
                        continue

                    touched_ids.add(fid)
                    local_touched += 1
                    name = str(feat["Nom"] or "").strip()
                    dossier = str(feat["Dossier"] or "").strip()
                    if not name:
                        self.log(f"Emprise {fid} ignorée : Nom vide.")
                        continue

                    raster_path = self._find_raster(raster_index, name, dossier)
                    if not raster_path:
                        missing_count += 1
                        self.log(f"ORTHO INTROUVABLE : Nom={name} | Dossier={dossier}")
                        continue

                    try:
                        copied_now, dst = self._copy_once(raster_path, self.output_folder, copied)
                        if copied_now:
                            copied_count += 1
                            self.log(f"COPIE : {Path(raster_path).name} -> {Path(dst).name}")
                    except Exception as e:
                        errors += 1
                        self.log(f"ERREUR COPIE : {Path(raster_path).name} -> {e}")

                self.log(
                    f"DXF GLOBAL : candidats bbox={len(candidate_ids)} | emprises retenues={local_touched} | "
                    f"orthos copiées={copied_count}"
                )
                self.progress_signal.emit(int(processed_geometries / max(total_geometries, 1) * 100))
                continue

            # ---------------------------------------------------------
            # SHP et DXF en mode géométrique classique
            # ---------------------------------------------------------
            self.log(f"Objets à contrôler : {len(geometries)}")

            for obj_no, plan_geom in enumerate(geometries, 1):
                bbox = plan_geom.boundingBox()
                candidate_ids = spatial_index.intersects(bbox)
                candidate_count += len(candidate_ids)

                for fid in candidate_ids:
                    feat = footprint_by_id.get(fid)
                    if feat is None:
                        continue

                    try:
                        footprint_geom = feat.geometry()
                        if not footprint_geom or footprint_geom.isEmpty():
                            continue
                        if footprint_geom.disjoint(plan_geom):
                            continue
                    except Exception as e:
                        errors += 1
                        self.log(f"ERREUR TEST GEOMETRIQUE : {e}")
                        continue

                    if fid in touched_ids:
                        continue

                    touched_ids.add(fid)
                    name = str(feat["Nom"] or "").strip()
                    dossier = str(feat["Dossier"] or "").strip()
                    if not name:
                        self.log(f"Emprise {fid} ignorée : Nom vide.")
                        continue

                    raster_path = self._find_raster(raster_index, name, dossier)
                    if not raster_path:
                        missing_count += 1
                        self.log(f"ORTHO INTROUVABLE : Nom={name} | Dossier={dossier}")
                        continue

                    try:
                        copied_now, dst = self._copy_once(raster_path, self.output_folder, copied)
                        if copied_now:
                            copied_count += 1
                            self.log(f"COPIE : {Path(raster_path).name} -> {Path(dst).name}")
                    except Exception as e:
                        errors += 1
                        self.log(f"ERREUR COPIE : {Path(raster_path).name} -> {e}")

                processed_geometries += 1
                if obj_no == 1 or obj_no % 100 == 0 or obj_no == len(geometries):
                    self.log(
                        f"Progression {Path(plan_path).name} : {obj_no}/{len(geometries)} objets | "
                        f"orthos copiées={copied_count}"
                    )
                    self.progress_signal.emit(int(processed_geometries / max(total_geometries, 1) * 100))

        self.log("================================")
        self.log("TRAITEMENT TERMINÉ")
        self.log(f"PLANS TRAITÉS : {total_plans}")
        self.log(f"OBJETS CONTRÔLÉS : {processed_geometries}")
        self.log(f"CANDIDATS INDEX : {candidate_count}")
        self.log(f"EMPRISES UNIQUES CONCERNÉES : {len(touched_ids)}")
        self.log(f"ORTHOS COPIÉES : {copied_count}")
        self.log(f"ORTHOS INTROUVABLES : {missing_count}")
        self.log(f"ERREURS : {errors}")
        self.log(f"DOSSIER SORTIE : {self.output_folder}")
        self.log("================================")


# =========================================================
# INTERFACE
# =========================================================

class App(QWidget):

    def __init__(self):

        super().__init__()
        self.setWindowTitle(
            "Geo Emprises"
        )

        # =============================================
        # ICON
        # =============================================

        if os.path.exists(ICON_PATH):
            self.setWindowIcon(
                QIcon(ICON_PATH)
            )

        # Fenêtre adaptable : la logique d'extraction reste strictement celle de V10.2.
        self.setMinimumSize(720, 560)
        self.resize(1000, 760)
        self.setSizePolicy(QSP_EXPANDING, QSP_EXPANDING)

        # =============================================
        # STYLE
        # =============================================

        self.setStyleSheet("""
            QWidget {
                background-color: #1e1e2f;
                color: white;
                font-size: 13px;
            }

            QPushButton {
                background-color: #4CAF50;
                padding: 8px;
                border-radius: 6px;
                font-weight: bold;
            }

            QPushButton:hover {
                background-color: #45a049;
            }

            QLineEdit,
            QComboBox,
            QListWidget {
                background-color: #2c2c3c;
                padding: 5px;
                border-radius: 5px;
            }

            QTextEdit {
                background-color: #12121a;
            }

            QProgressBar {
                border: 1px solid #555;
                border-radius: 5px;
                text-align: center;
            }

            QProgressBar::chunk {
                background-color: #4CAF50;
            }

            QTabWidget::pane {
                border: 1px solid #444;
            }

            QTabBar::tab {
                background: #2a2a3d;
                padding: 10px;
            }

            QTabBar::tab:selected {
                background: #4CAF50;
            }
        """)

        self.ui()

    # =====================================================
    # UI PRINCIPALE
    # =====================================================

    def ui(self):

        main_layout = QVBoxLayout()

        # =============================================
        # LOGO
        # =============================================

        logo = QLabel()

        if os.path.exists(LOGO_PATH):

            pixmap = QPixmap(LOGO_PATH)

            logo.setPixmap(
                pixmap.scaled(
                    180,
                    180,
                    QT_KEEP_ASPECT_RATIO,
                    QT_SMOOTH_TRANSFORMATION
                )
            )

        logo.setAlignment(QT_ALIGN_CENTER)
        logo.setMinimumHeight(90)
        logo.setMaximumHeight(110)
        logo.setStyleSheet("""
            background-color: transparent;
            border: none;
            padding: 4px;
        """)
        logo.setToolTip("DATAKAD")

        main_layout.addWidget(logo)

        # =============================================
        # DESCRIPTION COURTE
        # =============================================

        demo = QLabel("Génération et extraction d’orthophotos")
        demo.setAlignment(QT_ALIGN_CENTER)
        demo.setStyleSheet("""
            background-color: #2a2a3d;
            border-radius: 8px;
            padding: 6px;
            color: #dddddd;
            font-size: 12px;
        """)
        main_layout.addWidget(demo)

        # =============================================
        # TABS
        # =============================================

        tabs = QTabWidget()
        tabs.setSizePolicy(QSP_EXPANDING, QSP_EXPANDING)

        tab1 = QScrollArea()
        tab1.setWidgetResizable(True)
        tab1.setFrameShape(QSA_NO_FRAME)
        tab1.setWidget(self.ui_emprises())

        tab2 = QScrollArea()
        tab2.setWidgetResizable(True)
        tab2.setFrameShape(QSA_NO_FRAME)
        tab2.setWidget(self.ui_extraction())

        tabs.addTab(tab1, "1 - GÉNÉRER EMPPRISES")
        tabs.addTab(tab2, "2 - EXTRAIRE ORTHOPHOTOS")
        main_layout.addWidget(tabs, 1)

        # =============================================
        # BARRE DE REMARQUE
        # =============================================
        feedback_box = QWidget()
        feedback_layout = QHBoxLayout(feedback_box)
        feedback_layout.setContentsMargins(0, 4, 0, 4)
        feedback_label = QLabel("Remarque :")
        self.feedback_edit = QTextEdit()
        self.feedback_edit.setPlaceholderText("Votre remarque, anomalie ou proposition d'amélioration...")
        self.feedback_edit.setFixedHeight(48)
        btn_feedback = QPushButton("ENVOYER PAR E-MAIL")
        btn_feedback.setToolTip("Prépare un e-mail à envoyer à Anessmahir@gmail.com avec votre logiciel de messagerie.")
        btn_feedback.clicked.connect(self.send_feedback_direct)
        feedback_layout.addWidget(feedback_label)
        feedback_layout.addWidget(self.feedback_edit, 1)
        feedback_layout.addWidget(btn_feedback)
        main_layout.addWidget(feedback_box)

        # =============================================
        # COPYRIGHT
        # =============================================

        copyright_label = QLabel(
            "© DATAKAD 2026 — Aness MAHIR"
        )

        copyright_label.setAlignment(
            QT_ALIGN_CENTER
        )

        copyright_label.setStyleSheet("""
            color: #888;
            font-size: 10px;
            padding-top: 8px;
        """)

        main_layout.addWidget(
            copyright_label
        )

        self.setLayout(main_layout)

    # =====================================================
    # ONGLET EMPPRISES
    # =====================================================

    def ui_emprises(self):

        widget = QWidget()
        layout = QVBoxLayout(widget)

        self.inp = QLineEdit()
        self.out = QLineEdit()
        self.name = QLineEdit()
        self.year = QLineEdit()

        self.type = QComboBox()

        self.type.addItems([
            "LAZ",
            "JP2",
            "TIF",
            "TIFF"
        ])

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)

        self.progress = QProgressBar()

        btn_in = QPushButton("Parcourir")
        btn_out = QPushButton("Parcourir")
        btn_run = QPushButton("LANCER")
        btn_open = QPushButton(
            "OUVRIR DOSSIER SORTIE"
        )

        btn_in.clicked.connect(
            self.inp_folder
        )

        btn_out.clicked.connect(
            self.out_folder
        )

        btn_run.clicked.connect(
            self.run
        )

        btn_open.clicked.connect(
            self.open_output_folder
        )

        def row(label, field, button=None):

            r = QHBoxLayout()

            r.addWidget(
                QLabel(label)
            )

            r.addWidget(field)

            if button:
                r.addWidget(button)

            layout.addLayout(r)

        row(
            "Chemin Source",
            self.inp,
            btn_in
        )

        row(
            "Chemin Sortie",
            self.out,
            btn_out
        )

        row(
            "Nom D'acquisition",
            self.name
        )

        row(
            "Année",
            self.year
        )

        row(
            "Type",
            self.type
        )

        layout.addWidget(btn_run)
        layout.addWidget(btn_open)
        layout.addWidget(self.progress)
        layout.addWidget(self.log_box)

        return widget

    # =====================================================
    # ONGLET EXTRACTION
    # =====================================================

    def ui_extraction(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)

        def row(label, field, button=None):
            r = QHBoxLayout()
            r.addWidget(QLabel(label))
            r.addWidget(field)
            if button:
                r.addWidget(button)
            layout.addLayout(r)

        # =============================================
        # EMPPRISES EXISTANTES
        # =============================================
        self.ext_footprints = QLineEdit()
        btn_footprints = QPushButton("Sélectionner")
        btn_footprints.clicked.connect(self.select_footprints)
        row("Emprises existantes", self.ext_footprints, btn_footprints)

        # =============================================
        # PLANS SHP / DXF
        # =============================================
        layout.addWidget(QLabel("Plans SHP / DXF à traiter :"))
        self.dxf_list = QListWidget()
        layout.addWidget(self.dxf_list)

        plan_buttons = QHBoxLayout()
        btn_add = QPushButton("Ajouter SHP / DXF")
        btn_remove = QPushButton("Supprimer")
        btn_clear = QPushButton("Vider")
        btn_add.clicked.connect(self.add_dxf)
        btn_remove.clicked.connect(self.remove_dxf)
        btn_clear.clicked.connect(self.clear_dxf)
        plan_buttons.addWidget(btn_add)
        plan_buttons.addWidget(btn_remove)
        plan_buttons.addWidget(btn_clear)
        layout.addLayout(plan_buttons)

        self.ext_crs_info = QLabel(
            "Le CRS est détecté automatiquement. Si un DXF n'a pas de CRS, "
            "le plugin vous demandera de le sélectionner manuellement."
        )
        self.ext_crs_info.setWordWrap(True)
        self.ext_crs_info.setStyleSheet("color: #cccccc; padding: 6px;")
        layout.addWidget(self.ext_crs_info)

        # =============================================
        # OPTIONS DXF
        # =============================================
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode de sélection DXF :"))
        self.dxf_mode_combo = QComboBox()
        self.dxf_mode_combo.addItem("Zone du plan (recommandé)", "zone")
        self.dxf_mode_combo.addItem("Intersection précise des objets", "precis")
        self.dxf_mode_combo.setCurrentIndex(0)
        self.dxf_mode_combo.setToolTip(
            "Zone du plan : recherche les orthophotos qui intersectent le rectangle global du DXF. "
            "Intersection précise : recherche uniquement les orthophotos qui touchent réellement les objets DXF."
        )
        mode_row.addWidget(self.dxf_mode_combo, 1)
        layout.addLayout(mode_row)

        margin_row = QHBoxLayout()
        margin_row.addWidget(QLabel("Marge autour de la zone DXF (m) :"))
        self.dxf_margin = QDoubleSpinBox()
        self.dxf_margin.setRange(0.0, 10000.0)
        self.dxf_margin.setDecimals(2)
        self.dxf_margin.setSingleStep(1.0)
        self.dxf_margin.setValue(0.0)
        self.dxf_margin.setSuffix(" m")
        self.dxf_margin.setToolTip(
            "0 m = aucune marge. La marge est appliquée uniquement au mode Zone du plan."
        )
        margin_row.addWidget(self.dxf_margin)
        margin_row.addStretch()
        layout.addLayout(margin_row)

        # Description détaillée supprimée pour alléger l’interface.

        # =============================================
        # DOSSIERS
        # =============================================
        self.ext_raster_root = QLineEdit()
        btn_raster_root = QPushButton("Parcourir")
        btn_raster_root.clicked.connect(self.select_raster_root)
        row("Dossier orthophotos", self.ext_raster_root, btn_raster_root)

        self.ext_output = QLineEdit()
        btn_ext_output = QPushButton("Parcourir")
        btn_ext_output.clicked.connect(self.select_ext_output)
        row("Dossier sortie", self.ext_output, btn_ext_output)

        # =============================================
        # INFORMATIONS
        # =============================================
        # Les explications détaillées sont volontairement retirées de l’interface.
        # Les détails techniques restent disponibles dans le journal d’extraction.

        # =============================================
        # LANCEMENT
        # =============================================
        self.ext_progress = QProgressBar()
        self.ext_progress.setValue(0)
        self.ext_log = QTextEdit()
        self.ext_log.setReadOnly(True)
        self.btn_extract = QPushButton("LANCER L'EXTRACTION")
        self.btn_extract.clicked.connect(self.run_extraction)
        layout.addWidget(self.btn_extract)
        layout.addWidget(self.ext_progress)
        layout.addWidget(self.ext_log)

        self.progress_dialog = None
        return widget

    # =====================================================
    # EXTRACTION : SÉLECTIONS
    # =====================================================

    def select_footprints(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Sélectionner les emprises existantes",
            "",
            "Shapefile (*.shp);;GeoPackage (*.gpkg);;Tous les fichiers (*.*)"
        )
        if path:
            self.ext_footprints.setText(path)

    def _detect_plan_crs(self, path):
        """Détecte le CRS d'un SHP ou DXF depuis QGIS/OGR puis .prj/.qpj."""
        try:
            layer = QgsVectorLayer(path, Path(path).stem, "ogr")
            if layer.isValid() and layer.crs().isValid():
                return layer.crs()
        except (OSError, RuntimeError, ValueError) as exc:
            logging.debug("Détection du CRS par la couche impossible pour %s : %s", path, exc)
        return self._read_sidecar_crs(path)

    def _read_sidecar_crs(self, path):
        for suffix in ('.prj', '.qpj'):
            sidecar = Path(path).with_suffix(suffix)
            if not sidecar.exists():
                continue
            try:
                raw = sidecar.read_bytes()
                txt = raw.decode('utf-8-sig', errors='ignore').strip()
                if not txt:
                    txt = raw.decode('cp1252', errors='ignore').strip()
                crs = QgsCoordinateReferenceSystem()
                if txt and crs.createFromWkt(txt) and crs.isValid():
                    return crs
                crs = QgsCoordinateReferenceSystem.createFromUserInput(txt)
                if crs.isValid():
                    return crs
            except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
                logging.debug("Lecture du CRS annexe impossible pour %s : %s", sidecar, exc)
        return QgsCoordinateReferenceSystem()

    def _ask_manual_crs(self, path, apply_to_all=False):
        """Demande le CRS d'un plan sans CRS. Retourne (crs, apply_to_all)."""
        dialog = QDialog(self)
        dialog.setWindowTitle("CRS du plan sans projection")
        dialog.resize(620, 260)
        layout = QVBoxLayout(dialog)

        label = QLabel(
            f"Le fichier suivant ne possède pas de CRS reconnu :\n\n"
            f"{Path(path).name}\n\n"
            "Sélectionnez le CRS réel du DXF."
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        selector = QgsProjectionSelectionWidget(dialog)
        selector.setCrs(QgsCoordinateReferenceSystem("EPSG:2154"))
        layout.addWidget(selector)

        apply_box = QCheckBox("Appliquer ce CRS à tous les plans sans CRS de cette extraction")
        layout.addWidget(apply_box)

        buttons = QDialogButtonBox(QDBB_OK | QDBB_CANCEL)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog_exec(dialog) != QDIALOG_ACCEPTED:
            return QgsCoordinateReferenceSystem(), False
        crs = selector.crs()
        return crs if crs.isValid() else QgsCoordinateReferenceSystem(), apply_box.isChecked()

    def add_dxf(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Sélectionner les plans SHP / DXF",
            "",
            "Plans vectoriels (*.shp *.dxf);;Shapefile (*.shp);;DXF (*.dxf);;Tous les fichiers (*.*)"
        )

        existing = {self.dxf_list.item(i).text() for i in range(self.dxf_list.count())}
        for path in paths:
            if path in existing:
                continue
            crs = self._detect_plan_crs(path)
            # Un DXF sans CRS est autorisé : le CRS sera demandé au lancement.
            self.dxf_list.addItem(path)
            existing.add(path)

        if self.dxf_list.count():
            self.dxf_list.setCurrentRow(self.dxf_list.count() - 1)
            self.on_plan_selected()

    def on_plan_selected(self):
        item = self.dxf_list.currentItem()
        if not item:
            self.ext_crs_info.setText("Aucun plan sélectionné.")
            return
        path = item.text()
        crs = self._detect_plan_crs(path)
        if crs.isValid():
            self.ext_crs_info.setText(
                f"Fichier : {Path(path).name}\n"
                f"Type : {Path(path).suffix.upper().lstrip('.') }\n"
                f"CRS : {crs.authid() or crs.description()}\n"
                "Transformation automatique en mémoire vers le CRS des emprises si nécessaire."
            )
        else:
            self.ext_crs_info.setText(
                f"Fichier : {Path(path).name}\nCRS invalide ou absent.\n"
                "Le CRS pourra être sélectionné manuellement au lancement."
            )

    def remove_dxf(self):
        for item in self.dxf_list.selectedItems():
            self.dxf_list.takeItem(self.dxf_list.row(item))
        self.on_plan_selected()

    def clear_dxf(self):
        self.dxf_list.clear()
        self.on_plan_selected()

    def select_raster_root(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "Dossier contenant les orthophotos"
        )
        if path:
            self.ext_raster_root.setText(path)

    def select_ext_output(self):
        path = QFileDialog.getExistingDirectory(
            self,
            "Dossier de sortie"
        )
        if path:
            self.ext_output.setText(path)

    # =====================================================
    # EXTRACTION : FENÊTRE DE SUIVI
    # =====================================================

    def _create_progress_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Suivi du traitement - Geo Emprises")
        dialog.resize(760, 520)
        dialog.setModal(False)

        layout = QVBoxLayout(dialog)
        title = QLabel("Traitement de l'extraction en cours...")
        title.setStyleSheet("font-size: 15px; font-weight: bold; padding: 5px;")
        layout.addWidget(title)

        progress = QProgressBar()
        progress.setValue(0)
        layout.addWidget(progress)

        log = QTextEdit()
        log.setReadOnly(True)
        layout.addWidget(log, 1)

        buttons = QDialogButtonBox(QDBB_CLOSE)
        buttons.rejected.connect(dialog.close)
        buttons.button(QDBB_CLOSE).setEnabled(False)
        layout.addWidget(buttons)

        dialog.progress = progress
        dialog.log = log
        dialog.title_label = title
        dialog.close_button = buttons.button(QDBB_CLOSE)
        return dialog

    def _progress_log(self, message):
        self.ext_log.append(message)
        if self.progress_dialog is not None:
            self.progress_dialog.log.append(message)
            sb = self.progress_dialog.log.verticalScrollBar()
            sb.setValue(sb.maximum())

    # =====================================================
    # REMARQUES / ENVOI PAR E-MAIL
    # =====================================================

    def send_feedback_direct(self):
        """Ouvre le logiciel de messagerie par défaut avec un e-mail prérempli."""
        message = self.feedback_edit.toPlainText().strip()
        if not message:
            QMessageBox.warning(self, "Remarque", "Saisissez une remarque avant l'envoi.")
            return

        subject = "Geo Emprises - Remarque / amélioration"
        body = (
            "Remarque envoyée depuis Geo Emprises.\n\n"
            + message
            + "\n\nVersion du plugin : 11.4"
        )
        mailto = (
            "mailto:Anessmahir@gmail.com"
            "?subject=" + quote(subject)
            + "&body=" + quote(body)
        )

        try:
            opened = QDesktopServices.openUrl(QUrl(mailto))
            if opened:
                self.feedback_edit.clear()
                QMessageBox.information(
                    self,
                    "E-mail préparé",
                    "Votre logiciel de messagerie a été ouvert avec la remarque préremplie.\n\n"
                    "Il suffit de cliquer sur « Envoyer »."
                )
            else:
                QMessageBox.warning(
                    self,
                    "E-mail",
                    "Impossible d'ouvrir le logiciel de messagerie par défaut. "
                    "Vérifiez qu'un logiciel de messagerie est configuré sur le poste."
                )
        except Exception as e:
            QMessageBox.critical(
                self,
                "E-mail",
                f"Impossible d'ouvrir le logiciel de messagerie.\n\n{e}"
            )

    # =====================================================
    # EXTRACTION : LANCEMENT
    # =====================================================

    def run_extraction(self):
        footprints = self.ext_footprints.text().strip()
        raster_root = self.ext_raster_root.text().strip()
        output = self.ext_output.text().strip()
        plan_paths = [
            self.dxf_list.item(i).text()
            for i in range(self.dxf_list.count())
        ]

        if not footprints:
            QMessageBox.warning(self, "Erreur", "Sélectionnez le fichier des emprises existantes.")
            return
        if not os.path.exists(footprints):
            QMessageBox.warning(self, "Erreur", "Le fichier des emprises n'existe pas.")
            return
        if not plan_paths:
            QMessageBox.warning(self, "Erreur", "Sélectionnez au moins un fichier SHP ou DXF.")
            return
        if not raster_root or not os.path.isdir(raster_root):
            QMessageBox.warning(self, "Erreur", "Sélectionnez un dossier d'orthophotos valide.")
            return
        if not output:
            QMessageBox.warning(self, "Erreur", "Sélectionnez le dossier de sortie.")
            return

        # Les plans sans CRS peuvent recevoir un CRS manuellement.
        manual_crs = {}
        last_manual_crs = None
        apply_manual_to_all = False
        for path in plan_paths:
            crs = self._detect_plan_crs(path)
            if crs.isValid():
                continue

            if last_manual_crs is not None and apply_manual_to_all:
                manual_crs[str(Path(path).resolve())] = last_manual_crs
                continue

            chosen, apply_all = self._ask_manual_crs(path)
            if not chosen.isValid():
                QMessageBox.information(
                    self, "Traitement annulé",
                    f"Aucun CRS n'a été sélectionné pour :\n{Path(path).name}"
                )
                return

            manual_crs[str(Path(path).resolve())] = chosen
            last_manual_crs = chosen
            apply_manual_to_all = apply_all

        os.makedirs(output, exist_ok=True)
        self.ext_log.clear()
        self.ext_progress.setValue(0)
        self.btn_extract.setEnabled(False)

        self.progress_dialog = self._create_progress_dialog()
        self.progress_dialog.show()
        self.progress_dialog.raise_()
        self.progress_dialog.activateWindow()

        # Message initial dans les deux journaux.
        self._progress_log("Lancement du traitement...")
        self._progress_log(f"Plans sélectionnés : {len(plan_paths)}")
        self._progress_log("Mode CRS : détection automatique + sélection manuelle si nécessaire + transformation en mémoire vers le CRS des emprises.")
        self._progress_log(f"Mode DXF : {self.dxf_mode_combo.currentData()}")

        self.ext_worker = ExtractionWorker(
            footprints,
            plan_paths,
            raster_root,
            output,
            manual_crs,
            self.dxf_mode_combo.currentData() == "zone",
            self.dxf_margin.value(),
            self.dxf_mode_combo.currentData()
        )

        self.ext_worker.log_signal.connect(self._progress_log)
        self.ext_worker.progress_signal.connect(self.ext_progress.setValue)
        self.ext_worker.progress_signal.connect(self.progress_dialog.progress.setValue)
        self.ext_worker.finished_signal.connect(self.extraction_finished)
        self.ext_worker.start()

    def extraction_finished(self):
        self.btn_extract.setEnabled(True)
        self.ext_progress.setValue(100)

        if self.progress_dialog is not None:
            self.progress_dialog.progress.setValue(100)
            self.progress_dialog.title_label.setText("Traitement terminé")
            self.progress_dialog.close_button.setEnabled(True)

        QMessageBox.information(
            self,
            "Terminé",
            "Le traitement est terminé.\n\n"
            "Consultez la fenêtre de suivi pour le détail des plans, objets, candidats et orthophotos copiées."
        )

    # =====================================================
    # EMPPRISES
    # =====================================================

    def inp_folder(self):

        f = QFileDialog.getExistingDirectory(
            self
        )

        if f:
            self.inp.setText(f)

    def out_folder(self):

        f = QFileDialog.getExistingDirectory(
            self
        )

        if f:
            self.out.setText(f)

    def open_output_folder(self):

        if not self.out.text():

            QMessageBox.warning(
                self,
                "Erreur",
                "Aucun dossier sortie"
            )

            return

        shp_path = os.path.join(
            self.out.text(),
            f"{self.name.text()}.shp"
        )

        if os.path.exists(shp_path):

            subprocess.Popen(["explorer.exe", f"/select,{shp_path}"])

        else:

            subprocess.Popen(["explorer.exe", self.out.text()])

    def log(self, m):

        self.log_box.append(m)

    def run(self):

        if not self.inp.text():
            QMessageBox.warning(
                self,
                "Erreur",
                "Sélectionnez le dossier source."
            )
            return

        if not self.out.text():
            QMessageBox.warning(
                self,
                "Erreur",
                "Sélectionnez le dossier sortie."
            )
            return

        if not self.name.text():
            QMessageBox.warning(
                self,
                "Erreur",
                "Indiquez le nom d'acquisition."
            )
            return

        self.worker = Worker(
            self.inp.text(),
            self.out.text(),
            self.name.text(),
            self.year.text(),
            self.type.currentText()
        )

        self.worker.log_signal.connect(
            self.log
        )

        self.worker.progress_signal.connect(
            self.progress.setValue
        )

        self.worker.start()

    # =====================================================
    # EXTRACTION : SELECTIONS

# =========================================================
# FIN DU MODULE
# =========================================================

# L'application QGIS et sa boucle Qt sont gérées par QGIS.
