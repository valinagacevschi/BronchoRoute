"""Synthetic DICOM geometry tests for import_dicom.build_volume (no real patient data)."""

from pathlib import Path

import numpy as np
import pytest
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, CTImageStorage, generate_uid

from import_dicom import build_volume


ROWS = 6
COLS = 8
PIXEL_SPACING = (0.7, 0.8)  # row spacing, col spacing
SLICE_STEP = 1.25
N_SLICES = 5


def _expected_ras(ipp_lps: np.ndarray, iop: np.ndarray, i: int, j: int) -> np.ndarray:
    row = iop[:3]
    col = iop[3:]
    sx = PIXEL_SPACING[1]
    sy = PIXEL_SPACING[0]
    lps = ipp_lps + i * sx * row + j * sy * col
    return np.array([-lps[0], -lps[1], lps[2]], dtype=np.float64)


def write_series(
    directory: Path,
    *,
    ipp_z_values: list[float],
    iop: tuple[float, ...] = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0),
    ipp_xy: tuple[float, float] = (10.0, 20.0),
    ipp_y_drift: float = 0.0,
    shuffle: bool = False,
) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    series_uid = generate_uid()
    study_uid = generate_uid()
    paths: list[Path] = []
    iop_arr = np.asarray(iop, dtype=np.float64)
    for index, z in enumerate(ipp_z_values):
        y = ipp_xy[1] + index * ipp_y_drift
        pixels = np.full((ROWS, COLS), index * 100 + 7, dtype=np.int16)
        # Encode (col, row) so (i,j) mapping is checkable after transpose
        for r in range(ROWS):
            for c in range(COLS):
                pixels[r, c] = index * 1000 + r * 10 + c

        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = CTImageStorage
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        meta.ImplementationClassUID = generate_uid()

        ds = Dataset()
        ds.file_meta = meta
        ds.SOPClassUID = CTImageStorage
        ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        ds.Modality = "CT"
        ds.SeriesNumber = 1
        ds.InstanceNumber = index + 1
        ds.ImageOrientationPatient = [float(v) for v in iop_arr]
        ds.ImagePositionPatient = [float(ipp_xy[0]), float(y), float(z)]
        ds.PixelSpacing = [float(PIXEL_SPACING[0]), float(PIXEL_SPACING[1])]
        ds.SliceThickness = SLICE_STEP
        ds.Rows = ROWS
        ds.Columns = COLS
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 1
        ds.RescaleSlope = 1
        ds.RescaleIntercept = 0
        ds.PixelData = pixels.tobytes()

        path = directory / f"slice_{index:02d}.dcm"
        ds.save_as(path, enforce_file_format=True)
        paths.append(path)

    if shuffle:
        order = list(range(len(paths)))
        rng = np.random.default_rng(0)
        rng.shuffle(order)
        shuffled_dir = directory / "shuffled"
        shuffled_dir.mkdir()
        shuffled_paths = []
        for new_i, old_i in enumerate(order):
            dest = shuffled_dir / f"file_{new_i:02d}.dcm"
            dest.write_bytes(paths[old_i].read_bytes())
            shuffled_paths.append(dest)
        return shuffled_paths
    return paths


def _assert_volume(files: list[Path], ipp_z_values: list[float], iop: tuple[float, ...],
                   ipp_xy: tuple[float, float] = (10.0, 20.0), ipp_y_drift: float = 0.0):
    volume, affine = build_volume([str(p) for p in files])
    assert volume.shape == (COLS, ROWS, N_SLICES)

    iop_arr = np.asarray(iop, dtype=np.float64)
    row = iop_arr[:3]
    col = iop_arr[3:]
    normal = np.cross(row, col)
    normal = normal / np.linalg.norm(normal)

    ipps = []
    for index, z in enumerate(ipp_z_values):
        ipps.append(np.array([ipp_xy[0], ipp_xy[1] + index * ipp_y_drift, z], dtype=np.float64))
    order = np.argsort([float(ipp @ normal) for ipp in ipps])
    sorted_ipps = [ipps[i] for i in order]
    sorted_z = [ipp_z_values[i] for i in order]
    sorted_indices = [int(i) for i in order]

    # Highest IPP z must map to highest world z
    worlds_z = []
    for k in range(N_SLICES):
        world = affine @ np.array([0.0, 0.0, float(k), 1.0])
        worlds_z.append(float(world[2]))
    max_ipp_k = int(np.argmax(sorted_z))
    assert worlds_z[max_ipp_k] == max(worlds_z)

    for k, (ipp, src_index) in enumerate(zip(sorted_ipps, sorted_indices)):
        for i in (0, COLS - 1):
            for j in (0, ROWS - 1):
                expected = _expected_ras(ipp, iop_arr, i, j)
                got = (affine @ np.array([float(i), float(j), float(k), 1.0]))[:3]
                assert np.allclose(got, expected, atol=1e-6)
                # pixel_array[r, c] with r=j, c=i → volume[i, j, k]
                assert volume[i, j, k] == src_index * 1000 + j * 10 + i

    return volume, affine


def test_shuffled_files_ascending_ipp(tmp_path: Path):
    zs = [float(k) * SLICE_STEP for k in range(N_SLICES)]
    files = write_series(tmp_path / "asc", ipp_z_values=zs, shuffle=True)
    _assert_volume(files, zs, (1.0, 0.0, 0.0, 0.0, 1.0, 0.0))


def test_ipp_z_descending(tmp_path: Path):
    zs = [float(N_SLICES - 1 - k) * SLICE_STEP for k in range(N_SLICES)]
    files = write_series(tmp_path / "desc", ipp_z_values=zs, shuffle=False)
    volume, affine = _assert_volume(files, zs, (1.0, 0.0, 0.0, 0.0, 1.0, 0.0))
    # After sort by normal (+z), k=0 is lowest IPP z
    assert (affine @ np.array([0.0, 0.0, 0.0, 1.0]))[2] < (affine @ np.array([0.0, 0.0, N_SLICES - 1.0, 1.0]))[2]
    assert volume[0, 0, 0] // 1000 == N_SLICES - 1  # original index of lowest z
    assert volume[0, 0, -1] // 1000 == 0


def test_plus_x_row_minus_y_col(tmp_path: Path):
    # LPS: row +x, col -y → normal = cross = (0, 0, -1)
    iop = (1.0, 0.0, 0.0, 0.0, -1.0, 0.0)
    zs = [float(k) * SLICE_STEP for k in range(N_SLICES)]
    files = write_series(tmp_path / "flip_y", ipp_z_values=zs, iop=iop)
    _assert_volume(files, zs, iop)


def test_tilted_stack_refused(tmp_path: Path):
    zs = [float(k) * SLICE_STEP for k in range(N_SLICES)]
    files = write_series(tmp_path / "tilt", ipp_z_values=zs, ipp_y_drift=0.5)
    with pytest.raises(ValueError, match="non-uniform/tilted stack"):
        build_volume([str(p) for p in files])
