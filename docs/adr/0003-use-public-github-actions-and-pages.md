---
status: accepted
---

# Use a public repository with GitHub Actions and GitHub Pages

The project will run its daily pipeline in a standard GitHub-hosted Actions runner at 02:17 UTC (10:17 Asia/Shanghai), send one SMTP digest, and commit generated state only after successful delivery. The non-zero minute avoids GitHub Actions' common top-of-hour scheduling congestion, while the later hour follows arXiv's daily announcement window. The same workflow assembles and deploys a static-site artifact to GitHub Pages. This replaces the container service because it removes process supervision and server exposure work, at the accepted cost that source code, generated paper data, summaries, and the site are public. API keys and SMTP credentials remain only in GitHub Actions Secrets.
