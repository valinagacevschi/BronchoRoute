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

**Case Library**:
The processed Cases kept on the laptop so they can be reopened without reprocessing; it holds no source DICOM.
_Avoid_: cache, history, recent files

## Planning

**Lesion**:
The clinical target the clinician marks on the CT: one point with a diameter, fixed in patient space; one per Case.
_Avoid_: marker, target, nodule

**RouteTarget**:
The endpoint of the airway route that the CT demonstrates, chosen near the Lesion; the distance between them is the target gap.
_Avoid_: target, endpoint
