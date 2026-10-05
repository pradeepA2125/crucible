//! Talking to an authenticated agentd (spec §3.2, §3.4, §3.5): read the per-port token,
//! check the backend proves it holds that token, then POST with it — directly (no proxy),
//! never following a redirect, only ever to 127.0.0.1.

use hmac::{Hmac, Mac};
use sha2::Sha256;
use std::collections::hash_map::RandomState;
use std::fs::OpenOptions;
use std::hash::{BuildHasher, Hasher};
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[cfg(unix)]
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};

const TOKEN_LEN: usize = 43;

pub fn token_path(home: &Path, port: u16) -> PathBuf {
    home.join(".crucible").join("run").join(format!("agentd-{port}.token"))
}

pub fn read_token(home: &Path, port: u16) -> Option<String> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    options.custom_flags(libc::O_NOFOLLOW);
    let file = options.open(token_path(home, port)).ok()?;
    #[cfg(unix)]
    {
        let meta = file.metadata().ok()?; // the handle, never stat-then-open
        // SAFETY: getuid has no preconditions and cannot fail.
        let uid = unsafe { libc::getuid() };
        if !meta.is_file() || meta.uid() != uid || meta.mode() & 0o077 != 0 {
            return None;
        }
    }
    let mut text = String::new();
    file.take((TOKEN_LEN + 1) as u64).read_to_string(&mut text).ok()?;
    let valid = text.len() == TOKEN_LEN
        && text.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_');
    valid.then_some(text)
}

pub fn health_proof(token: &str, nonce: &str) -> String {
    let mut mac = Hmac::<Sha256>::new_from_slice(token.as_bytes()).expect("any key length");
    mac.update(b"crucible-health-v1\0");
    mac.update(nonce.as_bytes());
    hex::encode(mac.finalize().into_bytes())
}

pub fn normalize_backend_url(raw: &str) -> Option<(String, u16)> {
    let mut url = reqwest::Url::parse(raw.trim()).ok()?;
    if url.host_str() == Some("localhost") {
        url.set_host(Some("127.0.0.1")).ok()?;
    }
    if url.host_str() != Some("127.0.0.1") {
        return None;
    }
    let port = url.port_or_known_default()?;
    Some((url.as_str().trim_end_matches('/').to_string(), port))
}

fn nonce() -> String {
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    let mut out = String::new();
    for salt in 0..2u64 {
        let mut h = RandomState::new().build_hasher();
        h.write_u128(nanos);
        h.write_u64(salt);
        out.push_str(&format!("{:016x}", h.finish()));
    }
    out // 32 hex chars; uniqueness, not secrecy, is what a nonce needs here
}

pub struct BackendNotifier {
    home: PathBuf,
    client: reqwest::Client,
    verified_token: Arc<Mutex<Option<String>>>,
    logged_401_for: Arc<Mutex<Option<String>>>,
}

impl BackendNotifier {
    pub fn new(home: PathBuf) -> anyhow::Result<Self> {
        let client = reqwest::Client::builder()
            .no_proxy() // the default honours HTTP(S)_PROXY and would hand the token to a proxy
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(10))
            .build()?;
        Ok(Self {
            home,
            client,
            verified_token: Arc::new(Mutex::new(None)),
            logged_401_for: Arc::new(Mutex::new(None)),
        })
    }

    pub async fn notify_index_build(&self, backend_url: &str, workspace: &str) {
        let Some((base, port)) = normalize_backend_url(backend_url) else {
            tracing::warn!(url = %backend_url, "backend URL is not 127.0.0.1; index-build notification skipped");
            return;
        };
        let Some(token) = read_token(&self.home, port) else {
            tracing::debug!(port, "no readable backend token; index-build notification skipped");
            return;
        };
        let already = self.verified_token.lock().unwrap().as_deref() == Some(token.as_str());
        if !already {
            if !self.verify(&base, &token).await {
                return;
            }
            *self.verified_token.lock().unwrap() = Some(token.clone());
        }
        let result = self
            .client
            .post(format!("{base}/v1/index/build"))
            .bearer_auth(&token)
            .json(&serde_json::json!({ "workspace_path": workspace }))
            .send()
            .await;
        match result {
            Ok(resp) if resp.status().is_success() => {
                tracing::debug!(url = %base, "notified backend: index build accepted");
            }
            Ok(resp) if resp.status() == reqwest::StatusCode::UNAUTHORIZED => {
                let mut logged = self.logged_401_for.lock().unwrap();
                if logged.as_deref() != Some(token.as_str()) {
                    tracing::warn!(url = %base, "backend rejected the index-build token (401)");
                    *logged = Some(token.clone());
                }
                *self.verified_token.lock().unwrap() = None;
            }
            Ok(resp) => {
                tracing::warn!(url = %base, status = %resp.status(), "backend index-build notification returned non-2xx");
            }
            Err(err) => {
                *self.verified_token.lock().unwrap() = None;
                tracing::warn!(url = %base, error = %err, "backend index-build notification failed (backend may not be running)");
            }
        }
    }

    async fn verify(&self, base: &str, token: &str) -> bool {
        let nonce = nonce();
        let response = match self.client.get(format!("{base}/health?nonce={nonce}")).send().await {
            Ok(r) => r,
            Err(err) => {
                tracing::debug!(error = %err, "backend health probe failed");
                return false;
            }
        };
        let body: serde_json::Value = match response.json().await {
            Ok(v) => v,
            Err(_) => return false,
        };
        let ok = body.get("proof").and_then(|p| p.as_str()) == Some(health_proof(token, &nonce).as_str());
        if !ok {
            tracing::warn!(url = %base, "backend did not prove it holds the token; not sending it");
        }
        ok
    }
}
