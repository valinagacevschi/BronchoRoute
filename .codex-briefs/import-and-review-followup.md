Review findings. Fix all, do not commit. Same PATIENT DATA RULE as before.

1. render_overlays.py renders local-001 upside down (trachea at the bottom of coronal_mip.png).
   local-001_CT.nii.gz has axcodes ('L','P','I'); AeroPath is ('L','P','S'). Root-cause fix: load
   every volume through `nib.as_closest_canonical` (CT, expert, fallback) and orient from the
   canonical RAS grid, for slices AND the MIP. Delete the ad-hoc orient_plane flip logic if canonical
   makes it unnecessary. Re-render 3 and local-001 and confirm superior is up in both
   (state how you checked, e.g. trachea row index < carina row index in the MIP).
   Also the coronal MIP background is a CT max-projection (saturated white bone); make the MIP
   background a mean-intensity projection at lung window, or plain dark, so the tree is readable.
2. Series labels: import_dicom.py labels series by the DICOM SeriesNumber tag, but users see the
   folder names (~/Downloads/dicom/SERIES4 holds SeriesNumber 5 "TAP Nativ 1.25mm"; folder SERIES2 =
   SeriesNumber 3 "Lung"). Print and store both: `folder` (path relative to src, first differing
   dir) and `series_number`. `--series` accepts either a series number or a folder name.
   Fix local-001/case.json: series_number 5 + folder "SERIES4"; skipped lung series_number 3 +
   folder "SERIES2".
3. Skip reasons: replace "not selected" with the concrete reason relative to the pick: "thicker
   slices (5.00 mm)", "LUNG kernel", "BONEPLUS kernel", "contrast phase (Arterial)", "fewer slices".
   Print skipped lines sorted by series number.
4. Do not change build_graph (case 3 "9 named airways" is pre-existing: the counter includes extra
   names beyond the 8-name check; not a regression).

Verify: py_compile; test_bridge passes; `import_dicom.py ~/Downloads/dicom --dry-run` output pasted
(must pick folder SERIES4 / SeriesNumber 5); re-rendered file lists. Short summary.
