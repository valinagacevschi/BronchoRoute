# Lessons Learned

- Feature flags must cover writes from shared edit hooks as well as HTTP routes.
  Hiding legacy review endpoints alone still lets Lesion and route edits create
  versions. Guard the shared version hooks and test both empty and frozen state
  with the flag off.
