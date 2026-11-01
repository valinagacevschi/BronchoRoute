# Lung Navigator

Planning bronchoscopic routes to peripheral lung lesions from a patient's chest CT, run locally on the clinician's Mac.

## Imaging input

**Archive**:
The zip file or folder of DICOM data the user loads, as exported from a CT scanner or PACS; may hold several Studies.
_Avoid_: dicom, zip, scan

**Study**:
One imaging examination of the patient on one date, made of several Series.
_Avoid_: exam, scan

**Series**:
One reconstruction inside a Study (a given slice thickness, kernel and contrast phase).
_Avoid_: scan, dataset, volume

**CT**:
The imaging modality the app works with; a plain radiograph (CR) cannot be used.
_Avoid_: CR

## Working set

**Case**:
One processed Series together with everything derived from it (segmentation, airway model, routes, marks).
_Avoid_: patient, project, dicom

**Active Case**:
The single Case open in the app; loading another Archive replaces it.
_Avoid_: current dicom, session
