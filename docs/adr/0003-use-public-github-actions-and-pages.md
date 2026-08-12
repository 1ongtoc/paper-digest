---
status: accepted
---

# Use a public repository with GitHub Actions and GitHub Pages

The project will run its daily pipeline in a standard GitHub-hosted Actions runner at 00:00 UTC (08:00 Asia/Shanghai), send one SMTP digest, and commit generated state only after successful delivery. The same workflow assembles and deploys a static-site artifact to GitHub Pages. This replaces the container service because it removes process supervision and server exposure work, at the accepted cost that source code, generated paper data, summaries, and the site are public. API keys and SMTP credentials remain only in GitHub Actions Secrets.
