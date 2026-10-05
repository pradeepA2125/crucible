use crucible_indexer::backend_auth::{
    health_proof, normalize_backend_url, read_token, token_path, BackendNotifier,
};
use std::fs;
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

const TOKEN: &str = "kkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkkk"; // 43 × 'k'

fn put_token(home: &std::path::Path, port: u16, content: &str) {
    let path = token_path(home, port);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(&path, content).unwrap();
    #[cfg(unix)]
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
}

#[test]
fn proof_matches_the_backend() {
    assert_eq!(
        health_proof(TOKEN, "00112233445566778899aabbccddeeff"),
        "3d0b0b66aa8ba0eaac39f9a4c8bc9a8b2fe21fff8db87b61c0ea75eb183f5f15"
    );
}

#[test]
fn reads_only_a_well_formed_private_file() {
    let home = tempfile::tempdir().unwrap();
    put_token(home.path(), 1, TOKEN);
    assert_eq!(read_token(home.path(), 1).as_deref(), Some(TOKEN));
    put_token(home.path(), 2, &format!("{TOKEN}\n"));
    assert_eq!(read_token(home.path(), 2), None);
    assert_eq!(read_token(home.path(), 3), None);
    #[cfg(unix)]
    {
        put_token(home.path(), 4, TOKEN);
        fs::set_permissions(token_path(home.path(), 4), fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(read_token(home.path(), 4), None);
        std::os::unix::fs::symlink(token_path(home.path(), 1), token_path(home.path(), 5)).unwrap();
        assert_eq!(read_token(home.path(), 5), None);
    }
}

#[test]
fn normalizes_only_loopback() {
    assert_eq!(
        normalize_backend_url("http://localhost:8123/"),
        Some(("http://127.0.0.1:8123".to_string(), 8123))
    );
    assert_eq!(normalize_backend_url("http://example.com:8123"), None);
}

/// A one-connection-at-a-time HTTP stub: answers /health with a proof for `proof_token`
/// and records every request's raw text.
async fn stub_backend(proof_token: &'static str, post_status: u16) -> (u16, Arc<Mutex<Vec<String>>>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    let seen = Arc::new(Mutex::new(Vec::new()));
    let seen2 = seen.clone();
    tokio::spawn(async move {
        loop {
            let (mut sock, _) = listener.accept().await.unwrap();
            let mut buf = vec![0u8; 8192];
            let n = sock.read(&mut buf).await.unwrap();
            let req = String::from_utf8_lossy(&buf[..n]).to_string();
            seen2.lock().unwrap().push(req.clone());
            let (status, body) = if req.starts_with("GET /health?nonce=") {
                let nonce = req.split("nonce=").nth(1).unwrap().split_whitespace().next().unwrap();
                (200, format!("{{\"status\":\"ok\",\"proof\":\"{}\"}}", health_proof(proof_token, nonce)))
            } else {
                (post_status, "{}".to_string())
            };
            // A 307 carries a real location, so the redirect test proves the policy.
            let extra = if status == 307 { "location: /elsewhere\r\n" } else { "" };
            let resp = format!(
                "HTTP/1.1 {status} X\r\ncontent-type: application/json\r\n{extra}content-length: {}\r\nconnection: close\r\n\r\n{body}",
                body.len());
            sock.write_all(resp.as_bytes()).await.unwrap();
        }
    });
    (port, seen)
}

#[tokio::test]
async fn posts_with_the_token_after_a_valid_proof() {
    let (port, seen) = stub_backend(TOKEN, 202).await;
    let home = tempfile::tempdir().unwrap();
    put_token(home.path(), port, TOKEN);
    let notifier = BackendNotifier::new(home.path().to_path_buf()).unwrap();
    notifier.notify_index_build(&format!("http://localhost:{port}"), "/ws").await;
    let seen = seen.lock().unwrap().clone();
    assert!(seen[0].starts_with("GET /health?nonce="));
    assert!(!seen[0].to_lowercase().contains("authorization"));
    assert!(seen[1].starts_with("POST /v1/index/build"));
    assert!(seen[1].contains(&format!("authorization: Bearer {TOKEN}")));
    assert!(seen[1].to_lowercase().contains(&format!("host: 127.0.0.1:{port}")));
}

#[tokio::test]
async fn does_not_post_when_the_proof_is_wrong() {
    let (port, seen) = stub_backend("wwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwwww", 202).await;
    let home = tempfile::tempdir().unwrap();
    put_token(home.path(), port, TOKEN);
    let notifier = BackendNotifier::new(home.path().to_path_buf()).unwrap();
    notifier.notify_index_build(&format!("http://127.0.0.1:{port}"), "/ws").await;
    let seen = seen.lock().unwrap().clone();
    assert_eq!(seen.len(), 1);
}

#[tokio::test]
async fn verifies_once_per_token_and_picks_up_a_rewritten_token() {
    let (port, seen) = stub_backend(TOKEN, 202).await;
    let home = tempfile::tempdir().unwrap();
    put_token(home.path(), port, TOKEN);
    let notifier = BackendNotifier::new(home.path().to_path_buf()).unwrap();
    let base = format!("http://127.0.0.1:{port}");
    notifier.notify_index_build(&base, "/ws").await;
    notifier.notify_index_build(&base, "/ws").await;
    let health_calls = seen.lock().unwrap().iter().filter(|r| r.starts_with("GET /health")).count();
    assert_eq!(health_calls, 1);
}

#[tokio::test]
async fn does_not_follow_redirects() {
    // A 307 to /elsewhere must not be followed with the token.
    let (port, seen) = stub_backend(TOKEN, 307).await;
    let home = tempfile::tempdir().unwrap();
    put_token(home.path(), port, TOKEN);
    let notifier = BackendNotifier::new(home.path().to_path_buf()).unwrap();
    notifier.notify_index_build(&format!("http://127.0.0.1:{port}"), "/ws").await;
    assert_eq!(seen.lock().unwrap().len(), 2); // health + the one POST, no follow-up
}
