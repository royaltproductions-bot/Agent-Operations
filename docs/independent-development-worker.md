# Independent development worker

Goal: a GitHub Actions worker that uses the existing local Qwen runtime to propose and validate incremental, general-purpose commerce capabilities. The worker must run separately from the production commerce workflow and must not require ChatGPT to execute.

Safety and operational requirements: least-privilege permissions, dedicated development branches, bounded runtime, no production secrets during tests, no unreviewed deployment, no external spending, and no modification of unrelated projects.

Implementation sequence: inspect current workflow and runner setup; implement a bounded code-edit-and-test loop; produce reviewable pull requests; connect approved tools to the production commerce runner; verify live behavior before reporting commercial completion.

Status: design only; no autonomous code worker deployed.
