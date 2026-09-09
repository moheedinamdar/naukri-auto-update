# resume/

Put your resume **PDF** here: the tool uploads the newest `*.pdf` in this folder.

- Your resume is **personal data** and is **git-ignored**: only this note and
  `.gitkeep` are tracked, so your PDF is never committed to the public repo.
- If this folder is empty on the first run, `./run.sh` asks for the absolute path
  to your PDF and copies it here automatically (reused on every later run).
- Have more than one PDF? The **newest by modified time** is used. Pin a specific
  one with `NAUKRI_RESUME_NAME=my_resume.pdf` in `.env`.
