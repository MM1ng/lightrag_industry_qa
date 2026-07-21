CREATE UNIQUE INDEX ux_work_order_reviews_work_order_id
    ON work_order_reviews(work_order_id);

CREATE TRIGGER trg_reviewed_work_order_target_immutable
BEFORE UPDATE OF
    work_order_id,
    request_id,
    conversation_id,
    diagnosis_id,
    payload_json,
    status,
    executed,
    created_at
ON work_orders
WHEN EXISTS (
    SELECT 1 FROM work_order_reviews
    WHERE work_order_reviews.work_order_id = OLD.work_order_id
)
BEGIN
    SELECT RAISE(ABORT, 'reviewed work order target is immutable');
END;
