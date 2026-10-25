Review findings, fix both, do not commit:

1. eval_fallback.py passes `--fast` to run_totalsegmentator.py. Fast = 3 mm models; that destroys distal airways and was not asked for. Remove it. (The cache would also silently reuse full-res ts_out/fallback_1 under --fast, mixing resolutions.)
2. run_totalsegmentator.py --legacy: lung_vessels_LEGACY outputs `lung_vessels.nii.gz` and `lung_trachea_bronchia.nii.gz` (see totalsegmentator/map_to_binary.py "lung_vessels_LEGACY"), not lung_arteries/lung_veins. The expected list is wrong so the cache never hits. Fix the list.

Verify with py_compile and by rerunning `run_totalsegmentator.py data/extracted/AeroPath/1/1_CT_HR.nii.gz -o ts_out/fallback_1` (should print cached for both steps). Short summary.
