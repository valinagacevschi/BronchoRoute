# BronchoRoute

_Formerly Lung Navigator._

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

**Nodule candidate**:
A possible nodule produced by detection or segmentation and shown as a suggestion; it stays a candidate until a clinician confirms it, and only then becomes a Lesion.
_Avoid_: lesion, finding, nodule annotation

**RouteTarget**:
The endpoint of the airway route that the CT demonstrates, chosen near the Lesion; the distance between them is the target gap.
_Avoid_: target, endpoint

## Airway provenance

**CT-seen airway**:
Airway segmented directly from the CT; the only airway a route may follow.
_Avoid_: real airway, segmented airway

**Interpolated airway**:
A short bridge (≤ 4 mm) joining two CT-seen airway pieces; routable only once a clinician confirms it.
_Avoid_: estimated, bridged gap

**Estimated airway**:
An extension of up to 15 mm beyond the last CT-seen airway tip, inferred from the paired pulmonary artery; shown but never part of a route.
_Avoid_: interpolated, predicted, synthetic airway
