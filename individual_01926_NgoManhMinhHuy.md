# Member Role Report — Day 9: Multi Agent A2A

## 1. Thông tin cá nhân

| Thông tin       | Nội dung                |
| --------------- | ------------------------ |
| Họ và tên       | Ngô Mạnh Minh Huy        |
| MSSV            | 2A202601926               |
| Khóa/Lớp        | K4                        |
| Vai trò chính   | Thiết kế & triển khai toàn bộ pipeline multi-agent: data layer, policy engine, Policy Agent (LLM), Verifier, self-check/audit |
| Ngày hoàn thành | 2026-08-05                |

## 2. Vai trò và phạm vi công việc

### Phần việc sở hữu

| Module/deliverable | File/hàm phụ trách | Input nhận vào | Output bàn giao | Trạng thái |
| ------------------- | -------------------- | ---------------- | ------------------ | ------------ |
| Data layer (join CSV, tính delivery/handoff variance, payment reconciliation, customer history) | `src/data_layer.py` (`DataStore`, `build_case_facts`) | 5 file CSV Olist, `claimed_order_id` | `CaseFacts` (facts đã kiểm chứng, sẵn sàng cho policy) | Hoàn thành |
| EC_POLICY_V2 rule engine (6 nhánh primary issue theo thứ tự ưu tiên, secondary issues, action ordering, evidence id, cap mảng, rounding) | `src/policy_engine.py` (`classify`, `build_case`) | `CaseFacts` | JSON output đúng schema đề bài | Hoàn thành |
| Policy Agent (LLM) + Verifier đối chiếu | `src/agents.py`, `src/llm_client.py` | Facts đã tính + system prompt chỉ chứa facts (không có `customer_request.message`) | Ý kiến `primary_issue`/`confidence` độc lập; Verifier lấy engine làm nguồn sự thật | Hoàn thành |
| Orchestration + logging | `run.py` | `input/EC_*.json` | `output/EC_*.json`, `logging/trace.jsonl`, `logging/metadata.json` | Hoàn thành |
| Self-check độc lập trước khi nộp | `validate_output.py` | `output/EC_*.json` | PASS/FAIL theo schema, cap, format evidence id | Hoàn thành |
| Kiến trúc & tài liệu | `architecture.md` | — | Sơ đồ agent, vai trò, quyền truy cập, luồng handoff | Hoàn thành |

Toàn bộ 4 agent Customer/Order&Product/Payment/Delivery là code tất định (join + công thức cố định trong đề, không gọi model). Duy nhất Policy Agent gọi LLM; Verifier luôn ghi đè bằng `policy_engine.py` khi có bất đồng.

### Việc hỗ trợ ngoài phạm vi chính

| Hoạt động | Thành viên/module được hỗ trợ | Kết quả |
| ---------- | -------------------------------- | --------- |
| Không có mục riêng — toàn bộ phần kỹ thuật của case (data layer → policy engine → agent → verifier → output) do tôi trực tiếp triển khai trong phạm vi báo cáo này. Phần phối hợp/phân công khác của nhóm do các thành viên tương ứng tự báo cáo. | — | — |

## 3. Kết quả theo vai trò

| Nhiệm vụ đã thực hiện | File/hàm/artifact liên quan | Kết quả bàn giao | Cách xác minh |
| ------------------------ | ------------------------------ | ------------------- | ---------------- |
| Dựng data layer từ 5 CSV, tính delivery variance, handoff variance theo seller, payment reconciliation, customer history | `src/data_layer.py` | `CaseFacts` cho 50/50 case, khớp 100% với tính tay bằng pandas độc lập | `audit_full.py` (script riêng, không import lại code cũ) — 0 mismatch trên mọi field |
| Cài đặt bảng luật EC_POLICY_V2 đúng thứ tự ưu tiên 6 nhánh + secondary issues + action ordering + evidence id + cap mảng + rounding 2 chữ số | `src/policy_engine.py` | 50 file `output/EC_*.json` đúng schema | `validate_output.py` → PASS: 50 file, schema/limits/evidence-format OK |
| Tích hợp Policy Agent LLM (Groq `llama-3.1-8b-instant` 8B, sau so sánh thêm OpenAI `gpt-4.1-mini`) với Verifier đối chiếu | `src/agents.py`, `src/llm_client.py` | Đo agreement rate 2 model trên cùng 50 case | So sánh trực tiếp: Groq 8B đồng ý engine 26/50 case; `gpt-4.1-mini` đồng ý 50/50 |
| Phát hiện & sửa bug khiến điểm leaderboard thấp | `src/policy_engine.py`, `src/data_layer.py` | Điểm tăng 66.0234 → 79.2487 sau khi sửa và nộp lại | Diff giữa `output36.zip` (bản lỗi) và output sau sửa; script `audit_financial.py` |

**Output cụ thể:** 50 file `output/EC_001.json` … `output/EC_050.json`, mỗi file đúng 11 khóa top-level theo schema đề bài, đã qua `validate_output.py` (PASS) và `audit_full.py` (0 mismatch so với recompute độc lập bằng pandas).

## 4. Giải thích phần kỹ thuật đã thực hiện

### Vấn đề cần giải quyết

Từ input chỉ có `claimed_order_id` + message tự do của khách, dựng lại toàn bộ hồ sơ điều tra khiếu nại (entity liên quan, delivery analysis, payment reconciliation, root cause, evidence, phương án xử lý) theo policy `EC_POLICY_V2`, dựa hoàn toàn vào dữ liệu CSV kiểm chứng được — không suy diễn sự kiện không có trong dữ liệu, không tin lời khách hàng.

### Cách triển khai

Pipeline 6 agent với handoff tường minh qua một `CaseFacts` object dùng chung:

1. **Customer/Order&Product/Payment/Delivery Agent** (code tất định): join `orders/order_items/order_payments/customers/products`, tính `delivery_variance_hours`, `handoff_variance_hours` theo từng seller (so với `shipping_limit_date` sớm nhất), `expected_total_brl`/`difference_brl`/`reconciled`. Không gọi LLM vì đây là phép cộng/join thuần túy — dùng model chỉ tạo rủi ro hallucination số tiền/ngày giờ.
2. **Policy Agent** (LLM, model khai báo cứng trong `src/llm_client.py`): nhận facts đã tính (KHÔNG nhận `customer_request.message` — chặn prompt injection từ input khách hàng), áp bảng luật EC_POLICY_V2 để cho ý kiến độc lập về `primary_issue`/`case_status`/`confidence`.
3. **Verifier Agent** (code tất định, `policy_engine.classify`): tính lại độc lập bằng chính hàm đã cài bảng luật; nếu LLM lệch, engine vẫn quyết định `primary_issue` cuối, chỉ `confidence` bị hạ để phản ánh bất đồng. Toàn bộ output ghi file luôn xuất phát từ engine, không bao giờ từ text LLM sinh trực tiếp.

### Input, output và contract

| Thành phần | Mô tả |
| ------------ | ------- |
| Input | `input/EC_*.json` (`case_id`, `claimed_order_id`, `investigation_scope`, `policy_version`) + 5 CSV Olist |
| Output | `output/EC_*.json` đúng 11 khóa top-level theo schema mục 6 đề bài |
| Module phụ thuộc | `src/data_layer.py` → `src/policy_engine.py` → `src/agents.py` → `run.py` |
| Module sử dụng output | `validate_output.py` (self-check trước khi zip nộp) |
| Điều kiện lỗi cần xử lý | `claimed_order_id` không tồn tại trong `orders.csv` → trả `no_action`, `evidence_ids` rỗng, không suy diễn; order không có item row → `expected_total_brl`/`difference_brl`/`reconciled` = `null`, các mảng item/seller/product/category/seller-handoff = rỗng, còn `item_total_brl`/`freight_total_brl` = `0` (tổng trên tập rỗng, không phải trường hợp "không xác định được") |

### Cách xác minh

```bash
python run.py
python validate_output.py
```

- **Kết quả mong đợi:** 50 file `output/EC_*.json` sinh ra, đúng schema, không case nào lỗi.
- **Kết quả thực tế:** `run.py` chạy xong 50/50 case trong ~62s; `validate_output.py` → `PASS: 50 output files, schema/limits/evidence-format all OK`.
- **Artifact/log:** `output/EC_001.json`…`EC_050.json`, `logging/trace.jsonl` (450 dòng, 9 sự kiện/case), `logging/metadata.json` (model, framework, runtime, summary phân loại). Không chứa secret — `.env` đã gitignore.

## 5. Một quyết định kỹ thuật quan trọng

- **Bối cảnh:** Đề bài giới hạn mỗi agent chỉ được dùng model ≤10B tham số. Cần quyết định LLM đóng vai trò gì trong việc phân loại `primary_issue` — tự quyết định hay chỉ tham khảo.
- **Các phương án đã cân nhắc:**
  1. Để LLM (Policy Agent) tự quyết định `primary_issue` và ghi thẳng vào output.
  2. Cài lại toàn bộ bảng luật EC_POLICY_V2 thành hàm tất định (`policy_engine.classify`) làm nguồn sự thật; LLM chỉ đóng vai trò cho ý kiến độc lập (đối chiếu chéo) và ước lượng `confidence`.
- **Phương án đã chọn:** (2).
- **Lý do:** Correctness quan trọng hơn "cho LLM làm nhiều việc hơn". Bảng luật EC_POLICY_V2 là quyết định nhị phân dựa trên điều kiện rõ ràng (status, so sánh ngày, ngưỡng 0.10 BRL) — không có gì mơ hồ cần "suy luận ngôn ngữ tự nhiên"; để LLM tự quyết định chỉ thêm rủi ro sai mà không thêm giá trị.
- **Bằng chứng quyết định phù hợp:** Đo trực tiếp trên cùng 50 case: model ≤10B compliant (`llama-3.1-8b-instant` qua Groq) chỉ đồng ý với engine tính tay 26/50 case (hay nhầm `late_delivery_seller` ↔ `late_delivery_logistics`, bỏ sót `valid_split_payment`). Nếu để LLM tự quyết định thay vì chỉ cho ý kiến, gần một nửa số case đã bị phân loại sai ngay từ bước đầu tiên.

## 6. Một lỗi hoặc blocker đã xử lý

- **Triệu chứng/lỗi nguyên văn:** Nộp thử lần đầu (`output36.zip`) được chấm 66.0234 điểm tổng; riêng category "Phương án xử lý" (Financial resolution & actions) chỉ 54.2427 — thấp bất thường so với các category khác (66-68).
- **Lệnh hoặc bước tái hiện:** So sánh nội dung `output/EC_*.json` tại thời điểm chấm (trùng timestamp với `output36.zip`) với kết quả tính tay độc lập bằng pandas (script `audit_financial.py`, viết mới, không import lại `policy_engine.py` để tránh lặp lại cùng lỗi).
- **Nguyên nhân gốc:** Hàm `resolution_actions()` trong `src/policy_engine.py` build danh sách action phụ (`review_seller_handoff`, `verify_refund_completion`, …) nhưng **quên thêm action chính** (`issue_full_refund`/`refund_freight`/`explain_valid_split_payment`/`reject_late_refund`) vào đầu danh sách — lỗi xảy ra ở toàn bộ 50/50 case, đúng vào field bị category "Phương án xử lý" chấm.
- **Cách xử lý:** Sửa `resolution_actions()` để `actions = [primary_action]` là phần tử đầu tiên, sau đó mới nối các action phụ theo đúng thứ tự đề bài quy định. Nhân dịp audit toàn diện, sửa thêm 1 lỗi liên quan: `item_total_brl`/`freight_total_brl` trả `null` thay vì `0` cho 6 case không có item row (`unavailable_order_paid`) — đề bài chỉ yêu cầu `expected_total_brl`/`difference_brl`/`reconciled` là `null`, tổng trên tập rỗng vẫn phải là `0`.
- **Cách xác minh sau khi sửa:** Chạy lại `run.py` (50/50 case), `validate_output.py` → PASS; viết thêm `audit_full.py` đối chiếu **toàn bộ** field (không chỉ financial) bằng recompute độc lập — 0 mismatch. Nộp lại: điểm tổng 66.0234 → 79.2487; riêng "Phương án xử lý" 54.2427 → 75.3048 (+21.06, mức tăng lớn nhất trong 7 category).
- **Điều học được:** Một bug tập trung ở một trường hẹp nhưng áp dụng cho 100% case vẫn kéo điểm tổng đáng kể. Khi có tín hiệu bất thường theo category (một category tụt sâu hơn hẳn các category còn lại), nên viết script đối chiếu độc lập nhắm đúng field nghi vấn thay vì đọc lại spec chung chung — script độc lập (không tái sử dụng code đang bị nghi ngờ) mới thật sự bắt được lỗi logic kiểu "quên append".

## 7. Hiểu biết về luồng end-to-end

> **Lưu ý:** 5 câu hỏi gốc của template (nhắc tới "Crossref", "vector index", "freshness monitoring", "corrupted và repaired") thuộc về một lab khác (RAG/data-pipeline), không khớp với bài Multi-Agent A2A này — khả năng cao là lỗi copy template. Trả lời bên dưới giữ đúng **tinh thần** từng câu hỏi (hiểu data flow, ground-truth, quality check, vì sao dùng cùng test set, tiêu chí "sửa thành công") nhưng map vào đúng pipeline của bài này.

1. **Dữ liệu đi từ input đến output như thế nào?** `input/EC_*.json` chỉ chứa `claimed_order_id`. Coordinator tra `claimed_order_id` trong `orders.csv` để lấy `customer_id` → join `customers` lấy `customer_unique_id` → join `order_items`/`order_payments` lấy item, seller, payment → join `products` lấy category. Toàn bộ facts này đi qua 4 agent tất định, tới Policy Agent (LLM) để phân loại, qua Verifier đối chiếu với engine, cuối cùng Coordinator ghi `output/EC_*.json`.
2. **"Ground truth" và test set dùng để đo gì?** Không có ground-truth JSON riêng; ground truth chính là bảng luật EC_POLICY_V2 trong README — tôi dùng nó để cài `policy_engine.py` làm nguồn sự thật, rồi TỰ kiểm bằng cách viết một bản tính tay độc lập bằng pandas (không dùng lại code đang nghi ngờ) và so hai bên. Đây là cách "tạo ground truth" khi đề bài không phát sẵn.
3. **Quality check nào khác ngoài việc "chạy không lỗi"?** `validate_output.py` kiểm schema/cap mảng/format evidence-id/cross-consistency (evidence phải trỏ đúng entity của case, không leak sang case khác) — chạy sau `run.py`, trước khi zip nộp, độc lập với logic sinh output.
4. **Vì sao phải dùng cùng bộ 50 case để so sánh trước/sau khi sửa bug?** Nếu so điểm trên hai tập case khác nhau, không thể tách được phần cải thiện đến từ sửa bug hay đến từ việc case dễ/khó hơn. Giữ nguyên input, chỉ đổi code, rồi so điểm leaderboard 66.02 → 79.25 mới là bằng chứng hợp lệ cho việc sửa bug có tác dụng.
5. **"Sửa thành công" được xác nhận bằng artifact/metric nào?** Hai lớp: (a) nội bộ — `validate_output.py` PASS + `audit_full.py`/`audit_financial.py` 0 mismatch trên toàn bộ field của 50 case; (b) bên ngoài — điểm leaderboard thật tăng từ 66.0234 lên 79.2487, riêng category chứa field bị lỗi tăng +21.06, đúng bằng field đã sửa.

## 8. Cam kết của thành viên

Đánh dấu sau khi tự kiểm tra:

- [x] Nội dung báo cáo phản ánh đúng phần việc và mức hiểu của tôi.
- [x] Tôi có thể giải thích luồng end-to-end, không chỉ module mình phụ trách.
- [x] Tôi không ghi "đã chạy thành công" cho phần chưa được kiểm chứng.
- [x] Báo cáo không chứa `.env`, API key, token hoặc secret.
- [x] Báo cáo này không phải bản sao nguyên văn của báo cáo nhóm hoặc báo cáo thành viên khác.

**Họ và tên:** Ngô Mạnh Minh Huy
**Ngày xác nhận:** 2026-08-05
