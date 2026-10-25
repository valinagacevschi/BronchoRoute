# Lung Navigator — Evidence and Revision Notes

## Verified points

1. A bronchoscopy-simulation review reports that structured simulator training can provide a risk-free environment, support objective performance feedback, and improve technical outcomes; it also cautions that training and assessment should be structured and use validated tools. Source: Vieira, Camargos & Ibiapina (2022), *Bronchoscopy simulation training in the post-pandemic world*.[^1]

2. Human spatial navigation research supports a distinction between flexible, map-like (allocentric) representations and familiar stimulus–response route strategies. It identifies a role for hippocampal/entorhinal systems, environmental landmarks, and route planning, but this does not by itself establish clinical transfer to bronchoscopy. Source: Epstein et al. (2017), *The cognitive map in humans: Spatial navigation and beyond*.[^2]

3. CT-based virtual bronchoscopy relies on three-dimensional airway segmentation, centerline extraction, and endoluminal rendering. It is a suitable basis for pre-procedural planning or educational simulation. Source: Helferty et al. (2007), *Computer-based System for the Virtual-Endoscopic Guidance of Bronchoscopy*.[^3]

4. Navigation bronchoscopy workflows depend on input CT quality, three-dimensional airway reconstruction, virtual endoscopic views, and registration. A simplified single-slice Hough-circle tracking approach is not a clinically credible primary segmentation pipeline. Production-oriented work should use volumetric segmentation, centerline extraction, quality-control review, and clear clinical-use limits. Source: Pickering et al. (2018), *Electromagnetic navigation bronchoscopy: a comprehensive review*.[^4]

## Revision decisions

- Reframe the work as a **three-layer platform**: an entertainment-led exploration experience, anatomy learning, and a separately governed simulation-research track.
- Replace claims of “direct relevance” or implied clinical effectiveness with an evaluation plan and explicit non-clinical, non-diagnostic positioning.
- Replace the 2D projection of patient anatomy as the main clinical path with a native 3D airway representation. Retain the 2D game as a low-fidelity, high-accessibility training mode.
- Replace slice-wise circle detection as the core imaging plan with volumetric segmentation → topology cleanup → centerline/radius extraction → clinician quality assurance → de-identified export.
- Define measurable outcomes: anatomy recognition, route accuracy, time to target, wrong-turn rate, wall-contact proxy, retention, workload, and transfer assessment.
- Build data governance, consent/de-identification, access controls, and clinical/regulatory separation into the concept from the outset.
- Adopt BBT's anatomical rules and orientation aids [^5]: provenance and separately stored bridges (PRD §2, §6.2–6.3), Lesion ≠ RouteTarget with `target_gap_mm` (PRD §2, §7), and the endoluminal orientation aids (PRD §8.1). BBT found that global low-HU thresholding leaks into parenchyma and that Hessian/vesselness approaches were not reliable for airways; TotalSegmentator gave the best automated starting point, but distal airways still need review. These are practitioner findings from the author's prototype work, not a validation study.

[^5]: Toma T. *CT-based Bronchial Branch Tracing (BBT)*, v1.0.0, 2026. Zenodo, doi:10.5281/zenodo.23190857; https://github.com/ttoma1068/CT-Bronchial-Branch-Tracing (Apache-2.0). Lung Navigator adapts its ideas and does not include its code. Under its NOTICE, a modified distribution must identify itself as modified and must not imply the author's endorsement or clinical validation.

[^1]: https://pmc.ncbi.nlm.nih.gov/articles/PMC9064612/
[^2]: https://pmc.ncbi.nlm.nih.gov/articles/PMC6028313/
[^3]: https://pmc.ncbi.nlm.nih.gov/articles/PMC2131713/
[^4]: https://amj.amegroups.org/article/view/4758/html
