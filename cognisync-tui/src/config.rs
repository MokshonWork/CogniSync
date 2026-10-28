use std::{fs, path::PathBuf};

use anyhow::{bail, Context, Result};
use serde::Deserialize;

#[derive(Debug, Deserialize, Default)]
struct FileConfig {
    backend_url: Option<String>,
    allow_insecure_http: Option<bool>,
}

#[derive(Clone, Debug)]
pub struct Config {
    pub backend_url: String,
}

impl Config {
    pub fn load(cli_url: Option<String>) -> Result<Self> {
        let path = PathBuf::from("config.toml");
        let file: FileConfig = if path.exists() {
            toml::from_str(&fs::read_to_string(&path).context("reading config.toml")?)?
        } else {
            FileConfig::default()
        };
        let url = cli_url
            .or(file.backend_url)
            .unwrap_or_else(|| "http://localhost:8001".into());
        let url = url.trim_end_matches('/').to_string();
        if !url.starts_with("http://") && !url.starts_with("https://") {
            bail!("backend URL must begin with http:// or https://");
        }
        if url.starts_with("http://")
            && !url.contains("localhost")
            && !file.allow_insecure_http.unwrap_or(false)
        {
            bail!("refusing insecure remote HTTP; use HTTPS or set allow_insecure_http = true");
        }
        Ok(Self { backend_url: url })
    }
}
