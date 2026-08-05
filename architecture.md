# Kiến trúc Multi-Agent — EC_POLICY_V2 Dispute Resolution

## 1. Sơ đồ

```mermaid
flowchart TD
    IN["input/EC_*.json<br/>(claimed_order_id, message — KHÔNG tin cậy)"] --> COORD

    subgraph Coordinator["Coordinator (run.py / agents.run_case)"]
        direction TB
        VALID["Xác thực claimed_order_id\ntrong orders.csv"]
        CA["Customer Agent"]
        OPA["Order & Product Agent"]
        PA["Payment Agent"]
        DA["Delivery Agent"]
        POL["Policy Agent (LLM)"]
        VER["Verifier Agent"]
        VALID --> CA --> OPA --> PA --> DA --> POL --> VER
    end

    CSV[("data/*.csv\ncustomers, orders, items,\npayments, products")] -.join/lookup.-> VALID
    CSV -.join/lookup.-> CA
    CSV -.join/lookup.-> OPA
    CSV -.join/lookup.-> PA
    CSV -.join/lookup.-> DA

    ENGINE["policy_engine.py\n(EC_POLICY_V2, deterministic\nground truth)"] -.independent re-check.-> VER
    LLM["OpenAI API\ngpt-4.1-mini (param count undisclosed —\nsee §7 compliance note)"] --> POL

    VER --> OUT["output/EC_*.json"]
    VER --> TRACE["logging/trace.jsonl"]
```

## 2. Vai trò, quyền truy cập, output từng agent

| Agent | Loại | Quyền truy cập | Nhận từ | Bàn giao (handoff) |
| --- | --- | --- | --- | --- |
| **Coordinator** (`run.py`, `agents.run_case`) | code | đọc `input/`, ghi `output/` + `logging/` | — | điều phối toàn bộ pipeline, gộp output cuối |
| **Customer Agent** | deterministic (code) | `customers.csv`, `orders.csv` | Coordinator | `customer_unique_id`, `related_order_ids`, `repeat_customer` |
| **Order & Product Agent** | deterministic (code) | `order_items.csv`, `products.csv` | Coordinator | item/seller/product/category, `multi_item_order`, `multi_seller_order`, `multiple_categories` |
| **Payment Agent** | deterministic (code) | `order_payments.csv` | Order & Product Agent (item+freight total) | `payment_total_brl`, `expected_total_brl`, `difference_brl`, `reconciled`, `split_payment` |
| **Delivery Agent** | deterministic (code) | `orders.csv` (timestamps), item `shipping_limit_date` | Order & Product Agent | `delivery_variance_hours`, `late_delivery`, `seller_handoff_analysis`, `late_handoff_seller_ids` |
| **Policy Agent** | **LLM (`gpt-4.1-mini`, xem cảnh báo compliance ở §7)** | chỉ nhận **facts đã được các agent trên tính toán** (không có quyền đọc CSV thô, không thấy `customer_request.message`) | 4 agent trên | `primary_issue`, `case_status`, `confidence` (ý kiến độc lập, KHÔNG phải quyết định cuối) |
| **Verifier Agent** | deterministic (code, `policy_engine.classify`) | facts đầy đủ + ý kiến LLM | Policy Agent | đối chiếu ý kiến LLM với engine tính lại độc lập; **engine luôn thắng** khi lệch; điều chỉnh `confidence`; ép schema/limit/rounding/evidence-id trước khi ghi file |

## 3. Vì sao 4 agent đầu không gọi LLM

Customer/Order&Product/Payment/Delivery chỉ làm join bảng và cộng trừ số theo công thức đã cố định trong đề bài (không có gì để "suy luận"). Bài yêu cầu rõ: *"Hệ thống phải ưu tiên dữ liệu có thể kiểm chứng thay vì tin hoàn toàn vào lời khiếu nại hoặc tự tạo ra sự kiện không tồn tại."* — dùng LLM cho phép cộng/join tất định chỉ tạo thêm rủi ro hallucination số tiền/ngày giờ mà không mang lại lợi ích gì. LLM được dùng đúng một lần, ở bước duy nhất thật sự cần áp dụng một tập luật (EC_POLICY_V2) lên facts đã biết — đây là placement tận dụng model nhỏ (≤10B) đúng chỗ nó có giá trị, thay vì gọi cho đủ 6 agent theo hình thức.

## 4. Verifier là nguồn sự thật, không phải LLM

`policy_engine.py` cài đặt lại 100% bảng luật EC_POLICY_V2 (thứ tự ưu tiên 6 nhánh, secondary issues, action ordering, evidence id, rounding, limit mảng) như một hàm thuần Python độc lập với LLM. Policy Agent (LLM) chỉ được dùng để:

1. Cho một ý kiến `primary_issue`/`case_status` độc lập (đối chiếu chéo).
2. Ước lượng `confidence` — trường **duy nhất** trong schema có tính chủ quan thật sự.

Verifier so khớp hai kết quả: nếu LLM đồng ý với engine → `confidence` lấy theo LLM (đã clamp `[0,1]`); nếu lệch → engine vẫn quyết định `primary_issue` cuối cùng, `confidence` hạ xuống 0.7 để phản ánh bất đồng. Toàn bộ output ghi ra file luôn xuất phát từ `policy_engine.build_case()`, không bao giờ từ text LLM sinh ra trực tiếp.

**Kết quả đo thực tế trên 50 case** (xem `logging/metadata.json.summary`, tái lập được bằng `validate_output.py`): engine khớp 100% với phân loại tính tay bằng pandas độc lập (không hard gate nào bị vi phạm). Đo hai model làm Policy Agent trên cùng 50 case:

| Model | Provider | ≤10B xác minh được | Đồng ý với engine |
| --- | --- | --- | --- |
| `llama-3.1-8b-instant` | Groq | Có (8B, công khai) | 26/50 — hay nhầm `late_delivery_seller` ↔ `late_delivery_logistics`, bỏ sót `valid_split_payment` |
| `gpt-4.1-mini` | OpenAI | **Không** (OpenAI không công bố param count) | 50/50 |

Đây là bằng chứng cho quyết định kiến trúc "Verifier luôn thắng": nếu để LLM tự quyết định `primary_issue` thay vì chỉ cho ý kiến, model 8B compliant sẽ làm sai gần một nửa số case. `gpt-4.1-mini` đang được dùng làm model chính thức (đổi theo yêu cầu) vì đồng ý 50/50 với engine — nhưng vì output cuối luôn lấy từ `policy_engine.build_case()`, việc đổi model chỉ ảnh hưởng đến độ tin cậy của `confidence`, không ảnh hưởng đến tính đúng của các trường còn lại.

## 5. Chống input không đáng tin (prompt injection / claim giả)

- `claimed_order_id` được validate bằng cách tra `orders.csv` **trước khi** chạy bất kỳ agent nào; không tìm thấy → case trả về `no_action`, `evidence_ids: []`, không suy diễn.
- `customer_request.message` (free text tiếng Việt của khách) **không bao giờ** được đưa vào prompt của Policy Agent. Mọi quyết định policy chỉ dựa trên facts đã tính từ CSV. Điều này chặn kịch bản khách hàng chèn chỉ thị kiểu "hãy hoàn tiền 100%" vào message để thao túng model.
- System prompt của Policy Agent nói rõ: chỉ dùng facts được liệt kê, bỏ qua mọi "chỉ thị" nằm trong giá trị dữ liệu.
- Evidence ID chỉ được sinh từ ID thật đã join được từ CSV (không có ID nào do LLM tự đặt ra lọt vào `evidence_ids`).

## 6. Giới hạn schema & rounding

Toàn bộ cap mảng (5 order/item/payment/related-order/product/category, 3 seller/root-cause/responsible-party, 20 evidence, 5 action) và làm tròn 2 chữ số thập phân được áp dụng tập trung trong `policy_engine.py` (hàm `cap()`, `round2()`), không rải rác theo từng agent — một chỗ để sửa nếu đề bài đổi giới hạn. `validate_output.py` chạy độc lập sau `run.py` để tự kiểm 50 file output đúng schema/format/evidence-id/cap trước khi nộp.

## 7. Model

- Model chính thức hiện tại (`src/llm_client.py: MODEL_NAME/PROVIDER`): `gpt-4.1-mini` qua OpenAI API.
- **Cảnh báo compliance (đề bài mục 9.1 — "Mỗi agent chỉ được sử dụng model ≤10B tham số"):** OpenAI **không công bố** số tham số của `gpt-4.1-mini`, nên không thể chứng minh model này ≤10B. Đây là lựa chọn có chủ đích để tối đa độ chính xác của ý kiến Policy Agent (50/50 so với engine, xem §4), đổi lấy rủi ro nếu ban giám khảo áp dụng chặt điều khoản 9.1.
- Phương án thay thế tuân thủ chắc chắn: đổi `MODEL_NAME = "llama-3.1-8b-instant"`, `PROVIDER = "groq"` trong `src/llm_client.py` (Meta Llama 3.1, 8B tham số công khai, ≤10B) — hằng số `COMPARISON_MODEL_NAME`/`COMPARISON_PROVIDER` trong cùng file đã trỏ sẵn tới cặp này. Vì Verifier luôn ghi đè bằng `policy_engine.py`, đổi lại model này **không** làm thay đổi bất kỳ trường nào khác ngoài `confidence`.
- `.env` chứa `GROQ_API_KEY` và `OPENAI_API_KEY`. Model name khai báo cứng trong `src/llm_client.py` theo đúng yêu cầu đề bài (không đặt trong `.env`).
