# Changes: synthetic Archive builder shared by browser and Electron smoke tests.
from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
from pydicom.dataset import Dataset, FileDataset


def write_series(folder: Path, series_number: int, uid: str, n: int = 251):
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        path = folder / f"IM{i:04d}.dcm"
        file_meta = Dataset()
        file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        file_meta.MediaStorageSOPInstanceUID = f"{uid}.{i}"
        file_meta.TransferSyntaxUID = "1.2.840.10008.1.2"
        ds = FileDataset(str(path), {}, file_meta=file_meta, preamble=b"\0" * 128)
        ds.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        ds.SOPInstanceUID = f"{uid}.{i}"
        ds.SeriesInstanceUID = uid
        ds.StudyInstanceUID = "1.2.3.4.5"
        ds.StudyDate = "20260101"
        ds.StudyDescription = "Synthetic thorax"
        ds.Modality = "CT"
        ds.SeriesNumber = series_number
        ds.SeriesDescription = "AXIAL STANDARD"
        ds.ConvolutionKernel = ["Bf37f", "3"]
        ds.Manufacturer = "SIEMENS"
        ds.SliceThickness = 1.0
        ds.PixelSpacing = [1.0, 1.0]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.ImagePositionPatient = [0.0, 0.0, float(i)]
        ds.Rows = 8
        ds.Columns = 8
        ds.BitsAllocated = 16
        ds.BitsStored = 16
        ds.HighBit = 15
        ds.PixelRepresentation = 1
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.RescaleSlope = 1
        ds.RescaleIntercept = -1024
        ds.PixelData = (np.zeros((8, 8), dtype=np.int16)).tobytes()
        ds.save_as(path)


def write_archive(target: Path):
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        write_series(root / "one", 1, "1.2.826.0.1.3680043.10.999.1")
        write_series(root / "two", 2, "1.2.826.0.1.3680043.10.999.2", n=4)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for path in root.rglob("*.dcm"):
                output.write(path, path.relative_to(root))
            output.writestr("autorun.inf", b"ignored")
            output.writestr("run.exe", b"ignored")
            output.writestr("note.txt", b"ignored")
            output.writestr("__MACOSX/._one", b"ignored")
    return target


if __name__ == "__main__":
    write_archive(Path(sys.argv[1]))
