# Lessons Learned

- Series selection: ranking by slice step before checking coverage can prefer a partial
  gated reconstruction. Classify eligibility first, use the first convolution-kernel
  value, and rank survivors independently of file discovery order. Keep the header
  inventory regression test to pin the recommendation and rejection reasons.
