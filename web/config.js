// Front end configuration.
//
// AVAILABILITY_API_BASE points at a live API. Leave it empty to read only the published snapshots
// in ./data, which is the correct setting for a deployment that must work whether or not the data
// machine happens to be awake.
//
// When set, it must be an https:// origin. This page is served over HTTPS, and a browser will
// refuse to call a plain http:// endpoint from it.
window.AVAILABILITY_API_BASE = "";

// How long to wait for the live API before falling back to the published snapshot.
window.AVAILABILITY_API_TIMEOUT_MS = 2500;
