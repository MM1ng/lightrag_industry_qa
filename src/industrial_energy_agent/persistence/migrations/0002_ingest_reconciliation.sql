ALTER TABLE ingest_jobs ADD COLUMN remote_file_source TEXT;
ALTER TABLE ingest_jobs ADD COLUMN track_id TEXT;

CREATE UNIQUE INDEX idx_ingest_jobs_remote_file_source
    ON ingest_jobs(remote_file_source)
    WHERE remote_file_source IS NOT NULL;
