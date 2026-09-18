# Metrics remote-write preflight

Deployment validates the effective `vmagent` command before backup/pull and
again after the checkout changes. It rejects unset or placeholder destinations
without displaying the URL or any credentials. Internal receiver DNS names and
private addresses are supported; this is configuration validation, not an
external connectivity test.

Set the correct `VM_REMOTE_WRITE_URL` through the deployment's approved
configuration source. Preserve the existing persistent vmagent buffer. Run the
normal deployment procedure only after the preflight succeeds.

After rollout, verify fresh ERP series at the intended receiver and that the
vmagent pending buffer drains. A syntactically valid URL is not proof of remote
acceptance or ingestion. No receiver URL, credentials, buffers or live containers
are changed by this patch.
