# 보완 데이터베이스 테이블 정의서 — 설계안

상태: **미구현·검토용** · 작성일: 2026-10-26 · 대응 그림: [보완 ERD](DATABASE_ERD.md)

**이 문서의 테이블은 앞으로 만들 구조다. 현재 DB의 실제 스키마 설명이 아니며, 실행 가능한 DDL·마이그레이션을 포함하지 않는다.** 기존 DB·실행 코드·계산 단위는 변경하지 않는다.

## A. 공통 규약

### 자료형·표기

| 표기 | 제안 의미 |
|---|---|
| `I` | SQLite INTEGER. ID·버전·수량·사업연도 등에 사용 |
| `T` | SQLite TEXT. 문자열·ISO 날짜·UTC 저장 시각 |
| `D` | 정확한 십진 문자열을 저장하는 TEXT. 금액·단가이며 REAL/NUMERIC 자동 변환을 사용하지 않음 |
| `J` | 유효한 JSON을 담는 TEXT. 입력·증빙·확정 결과의 스냅샷 |
| `B` | INTEGER이며 0 또는 1 |
| `?` | NULL 허용. 나머지는 NOT NULL을 기본으로 함 |
| `PK` / `FK` / `UQ` | 기본키 / 외래키 / 유일성 제약 |

각 테이블에서 `x_id` PK는 별도 언급이 없으면 I이다. 생성 시각은 T형 UTC 시각이며 생성 시 필수다. 수정 가능한 마스터·상태·버전 선택 자료에는 `updated_at`도 둔다. 확정 사실에는 수정 시각 대신 새 이벤트를 추가한다. 빈 문자열을 식별자·필수 근거의 기본값으로 사용하지 않는다.

금액 D는 부호·소수점·최대 자릿수·유한값을 검증한 정규형으로 저장한다. SQL의 문자열 `SUM` 또는 REAL 캐스팅으로 원장 금액을 합산하지 않고 정확한 십진 연산을 사용한다. 수량 I는 현행 정수 주식 범위이며 소수주 지원은 별도 설계다. 원 단위 처리 기준이 확정되기 전에는 새 금액 스케일을 강제하지 않는다.

### 회사 경계·무결성

- 회사별 테이블은 `company_id`를 둔다. 자식이 `company_id`와 다른 부모의 ID를 함께 참조하지 못하도록 부모의 `(company_id, id)` UQ 및 자식의 복합 FK를 설계한다.
- 같은 원칙을 사업연도·계산 실행·검증표·승인·원장에 적용한다. `company_id` 하나만 일치한다고 다른 연도의 계산·승인을 연결하지 않는다.
- FK는 회사·자산·원천·결산 등 증빙에 대해 기본적으로 삭제 RESTRICT를 사용한다. 증빙을 지우는 CASCADE DELETE를 사용하지 않는다.
- 실제 연결마다 FK를 활성화해야 한다. 동시 저장은 회사의 `source_revision`을 비교하는 트랜잭션으로 충돌을 차단한다.
- 날짜 모양 검사만으로 실제 날짜의 유효성을 보장하지 않는다. 달력 날짜, 기간 포함 여부와 연도 경계는 서버에서 검증한다.
- FK·CHECK·UQ로 표현할 수 없는 기간 겹침·승인 시점·합계·상태 전이는 서버 트랜잭션 검증으로 명시한다. 구현 시 필요에 따라 트리거를 추가하되, 문서만으로 DB가 모든 규칙을 보장한다고 간주하지 않는다.

### 원천·정책 버전 공통 열

거래·평가·원가정책·OCI 정책·규칙 적용 검토에는 `version I`, `is_current B`, `supersedes_id I?`, `created_at T`를 둔다. 동일 논리 자료의 수정은 새 버전을 추가하고 이전 내용은 남긴다. 이전 행의 현재 버전 표시를 바꾸는 작업도 감사 로그에 기록한다.

원천의 `status`는 ACTIVE/VOID로 구분한다. VOID도 새 버전으로 남기고 이전 기록을 삭제하지 않는다. 단순 순서 변경이나 종목명 표시 변경 때문에 경제적 원천을 새 거래로 만들지는 않는다. 확정 연도 원천은 버전 추가·VOID 전환도 차단한다.

## B. 기초·원천 자료

### 1. `companies` — 회사·작업공간

| 열 | 형식·제약 | 내용 |
|---|---|---|
| company_id | PK | 회사 내부 식별자 |
| company_name | T | 회사명 또는 미확인 로컬 작업공간 명칭 |
| business_number | T? | 확인된 사업자번호. 최초 이전에서 임의 생성하지 않음 |
| fiscal_end_month | I, 1~12 | 기본 결산월 |
| source_revision | I, 0 이상 | 회사 원천·정책 저장 버전 |
| status | T, ACTIVE/ARCHIVED | 보관 상태. 삭제와 구분 |
| created_at / updated_at | T | 생성·갱신 시각 |

결산월 변경은 기존 확정 연도의 기간을 바꾸지 못한다. 회사 보관 처리도 원천 삭제를 뜻하지 않는다.

### 2. `fiscal_years` — 사업연도

| 열 | 형식·제약 | 내용 |
|---|---|---|
| fiscal_year_id | PK | 사업연도 식별자 |
| company_id | I, FK companies | 소속 회사 |
| fiscal_year | I | 종료연도 기준 명칭 |
| start_date / end_date | T | 확정된 기간 정의 |
| status | T, OPEN/CLOSED | 결산 상태 |
| created_at | T | 생성 시각 |

UQ `(company_id, fiscal_year)`. 같은 회사의 기간 겹침 금지, 시작일 ≤ 종료일, 연속 이월 가능 여부를 검사한다. 확정 연도 날짜 변경 금지. 단기 사업연도 등 현재 앱 범위 밖 기간을 이름만 보고 자동 추정하지 않는다.

### 3. `stock_master` — 공통 종목 목록

| 열 | 형식·제약 | 내용 |
|---|---|---|
| stock_id | PK | 코드와 분리한 종목 식별자 |
| stock_code / asset_name / market_type | T | 현재 코드·명칭·시장 |
| isin | T?, 값이 있으면 UQ | 확인된 증권 식별번호 |
| instrument_type / issuer_country / currency | T? | 확인된 상품·발행국·통화. 미확인을 보통주·국내로 위장하지 않음 |
| listing_status | T, LISTED/DELISTED/UNKNOWN | 상장 상태 |
| is_current_code | B | 현재 코드 매핑 여부 |
| source / fetched_at | T? | 수집 출처·시각. 수동/CSV도 구분 |
| created_at / updated_at | T | 관리 시각 |

현재 코드 매핑에 대해 `(market_type, stock_code)` 부분 UQ를 둔다. 동일 코드의 과거 매핑·상장폐지 자료는 지우지 않는다. 코드 재사용과 시장 이전은 확인 후 동일 `stock_id` 유지 또는 별도 종목 생성 여부를 판단한다. 자동 이름 매칭으로 서로 다른 권리의 주식을 병합하지 않는다.

### 4. `market_prices` — 필요한 날짜의 시세·정정 버전

| 열 | 형식·제약 | 내용 |
|---|---|---|
| price_id | PK | 특정 시세 버전 식별자 |
| stock_id | I, FK stock_master | 종목 |
| price_date / provider | T | 실제 시세일·공급자 |
| quote_revision | I, 1 이상 | 같은 종목·시세일·공급자의 정정 회차 |
| close_price | D, 0 이상 | 종가 |
| currency | T | 가격 통화 |
| payload_hash / raw_row_json | T / J | 정규화한 해당 종목 응답의 식별값·원문 |
| fetched_at | T | 해당 버전을 최초 저장한 시각 |
| supersedes_price_id | I?, FK self | 정정 전 시세 |

UQ `(stock_id, price_date, provider, quote_revision)` 및 `(stock_id, price_date, provider, payload_hash)`. 동일 내용 재조회는 기존 행을 재사용하고 가격 정정은 새 행으로 추가한다. 해시 입력에 조회 시각을 넣어 매번 새 가격이 생기게 하지 않는다. 참조된 시세의 값·출처·원문은 수정·삭제 금지.

### 5. `investment_assets` — 회사의 분류별 보유자산

| 열 | 형식·제약 | 내용 |
|---|---|---|
| asset_id | PK | 보유자산 ID |
| company_id / stock_id | I, FK | 회사·공통 종목 |
| classification | T, FVOCI/FVTPL | 회계분류 |
| book_cost_method | T | TOTAL/MOVING/FIFO/SPECIFIC 등 현행 회계 원가 참고 정책 |
| status | T, ACTIVE/ARCHIVED | 사용 상태 |
| created_at / updated_at | T | 관리 시각 |

현행 범위에서는 UQ `(company_id, stock_id, classification)`. 계좌별 별도 자산이 필요하면 키 확장을 먼저 검토한다. 원천 발생 후 분류·종목을 임의 수정하지 않는다. 종목 통합 세무원가와 개별 보유자산 회계장부를 구분한다.

### 6. `trade_events` — 매수·매도·배당 원천 버전

| 열 | 형식·제약 | 내용 |
|---|---|---|
| trade_id | PK | 원천의 특정 버전 ID |
| company_id / asset_id / fiscal_year_id | I, 복합 FK | 회사·보유자산·귀속 연도 |
| logical_key | T | 버전이 바뀌어도 유지하는 거래 식별값 |
| trade_date / event_type | T | 거래일, BUY/SELL/DIVIDEND |
| quantity | I? | 매수·매도는 양의 정수, 배당은 NULL 허용 |
| execution_price | D? | 실제 체결단가. 매수·매도는 0 이상 필수 |
| gross_amount | D, 0 이상 | 거래 총액 또는 배당 원천 금액 |
| fees / transaction_tax / net_cash_amount | D? | 부대비용·거래세·현금금액. 미확인을 0으로 단정하지 않음 |
| note / evidence_json | T? / J? | 메모·당시 종목명·수입 근거·증빙 등 |
| version / is_current / supersedes_id / status / created_at | 공통 규약 | 수정·VOID 이력 |

UQ `(company_id, logical_key, version)`, 현재 버전은 `(company_id, logical_key)` 부분 UQ. 날짜·수량·체결가·총액 대사, 보유수량 초과 매도, 비용 포함 관계를 검증한다. 비용·배당 관련 열을 설계했다고 현 계산 엔진이 이를 처리한다고 간주하지 않는다. 세법 귀속일 확인은 거래 종류에 맞는 규칙을 별도로 적용한다.

### 7. `valuation_events` — 평가 원천 버전

| 열 | 형식·제약 | 내용 |
|---|---|---|
| valuation_id | PK | 평가 특정 버전 ID |
| company_id / asset_id / fiscal_year_id | I, 복합 FK | 회사·평가 대상·연도 |
| logical_key / valuation_date | T | 평가 식별값·장부 평가일 |
| quantity | I, 양수 | 평가 대상 수량 |
| price_id | I?, FK market_prices | 사용한 API 시세 버전. 수동 가격이면 NULL 가능 |
| applied_price / fair_value | D | 실제 적용 단가·평가금액 |
| price_date / source | T | 실제 시세 기준일·가격 출처 |
| note / evidence_json | T? / J? | 가격 조정 사유·당시 명칭·증빙 |
| version / is_current / supersedes_id / status / created_at | 공통 규약 | 수정 이력 |

거래와 같은 논리키·버전 UQ, 현행 범위에서는 현재 ACTIVE 평가에 `(company_id, asset_id, valuation_date)` 부분 UQ. 사용한 시세의 종목과 보유자산의 종목 일치, 수량·평가금액·휴장일 기준일을 확인한다. API 가격과 다르게 적용하면 사유를 남긴다. 평가손익은 원천 입력의 권위값이 아니라 이전 장부를 기준으로 계산한다.

## C. 정책·적용 근거

### 8. `tax_cost_policy_versions` — 회사 세무원가 정책

열: `policy_id PK`, `company_id I FK`, `effective_start_year I?`, `is_legacy_baseline B`, `method T(TOTAL/MOVING)`, `filing_status T`, `filing_date T?`, `receipt T?`, `change_status T`, `previous_method T?`, `reviewer T?`, `reason T?`, `evidence_json J?`, 공통 버전 열.

신규 확인 정책은 확인자·사유 필수. 불완전한 과거 자료는 미확인 상태와 원문을 보존한다. 기존 공통 정책에만 시작연도 NULL과 `is_legacy_baseline=1`을 허용하고 과거 적용연도를 임의 생성하지 않는다. 현재 유효한 시작연도별 정책 및 회사별 공통 기준은 각각 부분 UQ. 분류별로 회사 세무방법을 달리 선택하는 구조로 만들지 않는다.

### 9. `fvoci_policy_versions` — 사업연도별 OCI 처분 정책

열: `policy_id PK`, `company_id I FK`, `effective_start_year I?`, `is_legacy_baseline B`, `disposal T(TRANSFER/RETAIN)`, `confirmation_status T(CONFIRMED/UNKNOWN)`, `reviewer T?`, `reason T?`, 공통 버전 열.

신규 연도별 정책은 시작연도·확인자·사유 필수. 시작연도부터 다음 정책 전까지 상속하며 결산월에 따른 사업연도 종료연도로 판단한다. 공통 기준 및 시작연도별 현재 버전에 부분 UQ. 확정 결산까지 영향을 줄 수 있는 소급 추가·수정·VOID를 차단한다. 과거 처분 유지분 OCI의 일괄 대체는 생성하지 않는다. [현재 정책 보존 원칙](FVOCI_POLICY_YEARS.md)을 유지한다.

### 10. `company_rule_reviews` — 회사·연도별 자동 규칙 적용 검토

열: `review_id PK`, `company_id I FK`, `fiscal_year_id I FK`, `rule_version T`, `enabled B`, `confirmed B`, `reviewer T?`, `reason T?`, `exclusions_json J`, `legal_sources_json J`, `checked_on T`, 공통 버전 열.

UQ `(company_id, fiscal_year_id, version)`, 해당 연도 현재 검토는 부분 UQ. 활성화된 확인에는 확인자·사유 필수이며 법령의 명칭·조문·시행본·열람일과 예외 확인을 보존한다. 미확인·예외는 적격 확인으로 변환하지 않는다. 확정 연도 근거 변경 금지. 법령 규칙의 실행 코드를 임의 문자열로 저장·실행하는 기능은 만들지 않는다.

### 11. `rounding_profiles` — 계산 단위·금액 처리 버전

열: `rounding_profile_id T PK`, `description T`, `parameters_json J`, `status T(LEGACY/DRAFT/APPROVED)`, `reviewer T?`, `reason T?`, `created_at T`.

사용된 프로필은 수정하지 않고 새 버전을 추가한다. 기존 자료는 현행 0.01원 계산 규칙의 LEGACY 프로필을 식별해 보존한다. 내부 정밀도·승인 금액 단위·반올림 방식·처리 시점·잔여액 순서를 별도 항목으로 정의해야 한다. **원 단위 반올림 또는 절사를 이번 설계에서 확정하지 않는다.** DRAFT 프로필로 운영 결산을 확정하지 않는다.

## D. 계산·검증

### 12. `calculation_runs` — 계산 실행과 입력 고정본

열: `run_id PK`, `company_id I FK`, `fiscal_year_id I FK`, `as_of T`, `source_revision I`, `engine_version T`, `rounding_profile_id T FK`, `inputs_json J`, `input_hash T`, `result_hash T?`, `status T(SUCCEEDED/FAILED)`, `error_json J?`, `created_at T`.

완료 실행은 덮어쓰지 않는다. 입력은 당시 거래·평가의 특정 버전, 정책·규칙 및 기존 호환 설정을 고정한다. 최신 원천을 참조해 과거 입력을 대체하지 않는다. 재실행은 새 ID이며 동일 입력 식별값에 일반 인덱스를 두되 실행 이력을 잃는 전역 UQ를 강제하지 않는다. SUCCEEDED에는 결과 식별값이 필수다. 신규 검증과 승인은 성공한 실행만 참조한다. 원문만 있는 이전 실행에는 이 테이블에 새 엔진의 성공 근거를 꾸며 넣지 않으며, LEGACY 검증·검증표와 원문 보관으로 구분한다.

### 13. `calculated_balances` — 실행별 보유자산 잔액

열: `balance_id PK`, `company_id I`, `run_id I FK`, `asset_id I FK`, `quantity I`, `book_value D`, `tax_value D`, `reserve D`, `oci D`, `active_oci D`, `retained_disposal_oci D`, `profit_loss D`, `dividend D`.

UQ `(run_id, asset_id)`. 회사 일치, 수량 음수 금지, `reserve = tax_value − book_value`, `oci = active_oci + retained_disposal_oci`를 해당 실행의 정확한 금액 처리 기준으로 확인한다. 전량 처분 후 회계장부·세무원가는 0이어야 하지만 과거 처분 유지분 OCI는 남을 수 있다. 완료 실행의 잔액을 최신값으로 갱신하지 않는다.

### 14. `calculated_stock_pools` — 실행별 종목 통합원가

열: `pool_id PK`, `company_id I`, `run_id I FK`, `stock_id I FK`, `quantity I`, `tax_value D`, `method_snapshot J`.

UQ `(run_id, stock_id)`. 같은 회사·동일 권리의 종목별 세무원가이며 회계분류별 원가 풀로 분리하지 않는다. 자산별 배분 합계와 종목 풀의 수량·원가를 대사한다. 현재 지원하지 않는 권리 변동을 코드 문자열이 같다는 이유로 합산하지 않는다.

### 15. `validation_runs` — 검증 실행

열: `validation_id PK`, `company_id I`, `run_id I? FK`, `fiscal_year_id I FK`, `source_revision I`, `evidence_state T(VERIFIED/LEGACY)`, `summary_json J`, `issues_json J`, `created_at T`.

신규 VERIFIED 검증에는 계산 실행이 필수이며 회사·연도·원천 버전이 일치해야 한다. 이전 근거가 부족한 LEGACY 검증만 계산 연결에 NULL을 허용한다. 검증 결과를 수정하지 않으며 재검증은 새 행이다. 실패·경고 결과도 보존한다. 신규 결산은 지정한 성공 계산의 오류 없는 VERIFIED 검증과 경고 확인을 요구한다.

### 16. `issue_reviews` — 검증 이슈의 현재 검토 상태

열: `issue_id PK`, `company_id I FK`, `fiscal_year_id I FK`, `issue_code T`, `source_ref T`, `validation_id I FK`, `status T(OPEN/REVIEWING/RESOLVED/ACCEPTED)`, `reason T`, `updated_at T`.

UQ `(company_id, fiscal_year_id, issue_code, source_ref)`. 현행 전역 `issue_key`만 사용해 회사·연도 사이 상태가 섞이지 않도록 보완한다. 최신 검토를 나타내며 근거 검증 실행에 해당 이슈가 실제 존재해야 한다.

### 17. `issue_review_events` — 이슈 검토 이력

열: `event_id PK`, `company_id I`, `issue_id I FK`, `validation_id I FK`, `previous_status T?`, `status T`, `actor T`, `reason T`, `created_at T`.

추가 전용. 검토 상태 갱신과 이벤트 추가를 같은 트랜잭션에서 수행한다. 검토 완료 표시는 계산 오류를 덮어쓰거나 결산 차단을 해제하는 수단이 아니다.

## E. 검증표·조정 행·승인

### 18. `tax_workpapers` — 원천별 검증표 회차

| 열 | 형식·제약 | 내용 |
|---|---|---|
| workpaper_id | PK | 검증표 회차 ID |
| company_id / fiscal_year_id | I, 복합 FK | 회사·연도 |
| run_id / evidence_state | I? FK / T(VERIFIED/LEGACY) | 신규 VERIFIED 검증표는 성공한 계산 연결 필수. LEGACY에서만 NULL 허용 |
| trade_id | I?, FK trade_events | 거래 원천 특정 버전 |
| valuation_id | I?, FK valuation_events | 평가 원천 특정 버전 |
| rule_review_id | I?, FK company_rule_reviews | 자동 규칙 적용 근거. 수동 검토는 별도 명시 |
| cycle | I, 1 이상 | 취소·재검토 회차 |
| status | T | DRAFT/APPROVED/CANCELLED/SUPERSEDED |
| candidate_json / checks_json / rule_snapshot_json | J | 계산 후보·점검·규칙 및 조문 고정본 |
| created_at | T | 생성 시각 |

`trade_id`와 `valuation_id`는 정확히 하나만 값이 있어야 한다. NULL을 포함한 일반 UQ만으로 중복 방지를 구현하지 않는다. 거래용 `(company_id, fiscal_year_id, run_id, trade_id, cycle)` 부분 UQ와 평가용 대응 부분 UQ를 각각 둔다. 회사·자산·연도·원천 버전·계산 입력 간 일치 검사를 수행한다.

LEGACY이며 `run_id`가 없는 경우에는 같은 부분 UQ에서 run_id를 제외한 별도 이전용 중복 방지 키를 사용한다. 원천 자체를 연결하지 못하는 자료는 이 테이블의 정상 검증표로 강제 삽입하지 않고 원문·미해결 매핑으로 보관하여 전환을 중단한다. LEGACY 검증표는 새 승인·결산에 재사용하지 않고 현재 근거로 별도 검토 회차를 생성한다.

### 19. `tax_adjustment_rows` — 원인별 복수 조정 행

| 열 | 형식·제약 | 내용 |
|---|---|---|
| adjustment_row_id | PK | 조정 행 ID |
| company_id / workpaper_id / asset_id | I, 복합 FK | 회사·검증표·조정 대상 자산 |
| row_no | I, 1 이상 | 검증표 내 순번 |
| rule_id / label | T? / T | 자동 규칙 식별자·조정 원인 |
| direction | T | 익금산입/익금불산입/손금산입/손금불산입 |
| disposition | T | 유보/△유보/기타/기타사외유출/상여/배당 등 지원 목록 |
| amount | D, 양수 | 조정 금액. 0원 행은 만들지 않음 |
| reserve_movement | D | 부호 있는 유보 증감, 유보 외 처분이면 0 |
| legal_basis | T | 적용 조문·조건·검토 근거 |
| created_at | T | 생성 시각 |

UQ `(workpaper_id, row_no)`. 승인 전에는 초안 행을 편집하고 승인 후에는 고정한다. 대상 자산은 같은 회사여야 한다. 과세소득 증감과 유보 증감은 서로 다른 합계다. 방향·처분·금액·부호 및 자동 규칙 모드의 행 구성까지 검증한다. `기타` 잔차를 임의로 넣어 숫자를 맞추지 않는다.

### 20. `workpaper_decisions` — 승인·취소 사실

열: `decision_id PK`, `company_id I`, `workpaper_id I FK`, `action T(APPROVE/CANCEL)`, `cancel_of_decision_id I? FK self`, `actor T`, `reason T`, `acknowledged B`, `source_revision I`, `proof_json J`, `approved_rows_json J?`, `created_at T`.

검증표 회차당 APPROVE 한 건을 부분 UQ로 보장한다. CANCEL은 같은 검증표의 APPROVE를 참조하며 승인당 취소 한 건만 허용한다. 승인에는 명시적 확인·행 스냅샷·계산 및 규칙 근거 필수. 승인 행 고정·원장 전기·상태 변경·이벤트를 원자적으로 처리한다. 취소 후 같은 회차를 덮어쓰지 않고 다음 검토 회차를 만든다.

### 21. `workpaper_events` — 검증표 진행 이력

열: `event_id PK`, `company_id I`, `workpaper_id I FK`, `decision_id I? FK`, `event_type T`, `detail_json J`, `actor T`, `created_at T`.

생성·초안 수정·대체·승인·취소 등을 추가 전용으로 남긴다. 기존 `tax_workpaper_corrections`의 취소 사유와 이전 승인 원문은 취소 결정·스냅샷 및 원문 보관에 함께 대응시킨다. 원본 이력을 삭제하지 않는다.

## F. 유보원장·결산

### 22. `reserve_entries` — 승인 원장과 역분개

| 열 | 형식·제약 | 내용 |
|---|---|---|
| entry_id | PK | 원장 행 ID |
| company_id / fiscal_year_id / asset_id | I, 복합 FK | 회사·연도·대상 자산 |
| decision_id / adjustment_row_id | I, 복합 FK | 전기 또는 취소 결정·원래 조정 행 |
| entry_type | T, POST/REVERSAL | 승인 전기·취소 역분개 |
| reversal_of_entry_id | I?, FK self | 역분개 대상 원본 행 |
| adjustment_key | T | 평가·거래 등 유보 항목 |
| amount | D, 0이 아님 | 부호 있는 증감 |
| memo / recorded_at | T | 검토 메모·기록 시각 |
| legacy_source_ref | T? | 이전 원천 참조 문자열 보존 |

UQ `(decision_id, adjustment_row_id)`, 역분개 대상에는 부분 UQ. POST는 APPROVE 결정 및 유보 조정 행과 연결되고 금액이 해당 행의 증감과 일치해야 한다. REVERSAL은 CANCEL 결정 및 원본 POST에 연결되며 금액이 원본의 정확한 반대여야 한다. 역분개의 역분개·다른 회사/연도의 원본 참조·중복 전기 금지. 확정 연도 전기·취소 금지. 유보 외 조정은 원장 행을 만들지 않는다.

### 23. `fiscal_closes` — 확정 결산 고정본

열: `close_id PK`, `company_id I FK`, `fiscal_year_id I FK`, `run_id I? FK`, `validation_id I? FK`, `prior_close_id I? FK self`, `source_revision I`, `evidence_state T(VERIFIED/LEGACY)`, `opening_json J`, `movements_json J`, `ending_json J`, `positions_json J?`, `policy_snapshot_json J?`, `proof_json J`, `reviewer T`, `note T`, `closed_at T`.

UQ `(company_id, fiscal_year_id)`, 값이 있는 `prior_close_id`에도 부분 UQ. 신규 VERIFIED 확정에는 같은 회사·연도·계산의 `run_id`, `validation_id`, 잔액·정책 스냅샷이 필수다. 원장 항목별 기초·증감·기말, 자산별 장부 차이와 통합원가까지 대사한다. 전기 확정이 있으면 그 기말과 당기 기초를 연결한다. 최초 연도에 전기 근거가 없다는 이유만으로 확인되지 않은 기초 잔액을 0으로 만들지 않는다.

LEGACY 확정은 실제 기존 근거를 그대로 보존하고 없는 계산·정책 스냅샷에는 NULL을 허용한다. 이 경우 신규 VERIFIED 확정으로 승격하지 않는다. 확정 행과 금액 스냅샷은 수정·삭제 금지이며 정책·반올림 변경으로 다시 작성하지 않는다.

### 24. `closing_events` — 결산 관련 이력

열: `event_id PK`, `company_id I`, `fiscal_year_id I FK`, `close_id I? FK`, `event_type T`, `detail_json J`, `actor T`, `created_at T`.

추가 전용. 기존 이벤트 원문·정책 스냅샷을 보존한다. 신규 이벤트는 확정 계산·정책·승인 대사 근거를 기록한다. `close_id` NULL은 이전 원문 보관이나 확정 이전 사건을 위한 것이며, 새 CLOSE 이벤트는 실제 결산을 참조해야 한다. 재개방 이벤트와 새 결산 회차 모델은 별도 승인 전까지 지원하지 않는다.

## G. 감사·이전 자료

### 25. `audit_log` — 원천·정책 변경 이력

열: `audit_id PK`, `company_id I FK`, `source_revision I`, `entity_type T`, `entity_key T`, `action T(CREATE/UPDATE/VOID/SELECT_VERSION/IMPORT)`, `before_json J?`, `after_json J?`, `actor T`, `reason T?`, `recorded_at T`.

원천 저장과 같은 트랜잭션에서 추가한다. 현재 로그에 기록된 actor는 로컬 사용자 표기일 수 있으며 인증된 사용자 ID로 오해하지 않는다. 파일 소유자의 직접 SQL 조작까지 막는 구조는 아니다. 기존 로그의 action 명칭·시각·원문은 변환 과정에서 보관한다.

### 26. `legacy_archives` — 기존 자료의 읽기 전용 원문

열: `archive_id PK`, `company_id I FK`, `source_revision I?`, `backup_hash T`, `source_table T`, `source_key T`, `payload_json J`, `archived_at T`.

UQ `(backup_hash, source_table, source_key)`. 원본 SQLite 파일은 별도 백업으로 보존하고 이 테이블에는 작업공간 JSON과 기존 업무 테이블 행의 원문을 남긴다. 해시만 저장하고 원문을 버리는 구조가 아니다. 예전 근거에 없던 정책 확인·계산 성공·승인자를 새로 꾸며 넣지 않는다.

### 27. `migration_id_map` — 기존 ID와 새 ID 대응

열: `map_id PK`, `archive_id I FK`, `company_id I FK`, `source_entity T`, `source_key T`, `target_table T?`, `target_key T?`, `mapping_state T(MAPPED/REVIEW_REQUIRED)`, `note T?`, `created_at T`.

MAPPED에는 대상 테이블·키 필수이며 UQ `(archive_id, source_entity, source_key, target_table, target_key)`. 미해결 행에는 `(archive_id, source_entity, source_key)` 부분 UQ를 둔다. 거래 ID 7과 평가 ID 7을 같은 원천으로 취급하지 않도록 종류를 포함한다. 다형적 대상 문자열이므로 이 테이블의 대상 참조는 일반 FK만으로 검증할 수 없다. 이전 검증기가 실제 대상 행·회사·금액·관계의 존재를 확인한다. 매핑 중복·누락을 해결하기 전 운영 전환 금지.

### 28. `legacy_manual_adjustments` — 이전 수동 조정 원문

열: `legacy_adjustment_id PK`, `company_id I FK`, `archive_id I FK`, `legacy_key T`, `asset_id I? FK`, `date T?`, `payload_json J`, `mapping_state T(MAPPED/REVIEW_REQUIRED)`, `created_at T`.

UQ `(company_id, archive_id, legacy_key)`. 기존 `workspace.payload.adjustments`를 보존한다. 승인된 세무조정 행·유보 전기로 임의 변환하지 않는다. 연결되지 않는 원천이나 과거 잔액에 영향을 주는 항목은 검토 대상으로 남기고 운영 전환을 차단한다. 읽기 전용 이전 자료 화면과 계산 입력 어댑터에서 어떻게 취급할지 개별 대사 후 결정한다.

## H. 필수 인덱스와 상태 통제

| 조회·통제 목적 | 제안 인덱스 또는 검증 |
|---|---|
| 종목 검색 | 종목코드·명칭 검색용 인덱스. 검색 방식별 성능 확인 후 확정 |
| 특정 날짜 시세 | `(stock_id, price_date, provider, quote_revision)` |
| 거래·평가 순서 | `(company_id, asset_id, trade_date, trade_id)` 및 평가 대응 키 |
| 정책 상속 | 회사·시작연도·현재 버전 부분 인덱스 |
| 원천 최신 버전 | 회사·논리키의 현재 행 부분 UQ |
| 계산·검증 이력 | 회사·연도·원천 버전·생성 시각 |
| 미승인 검증표 | 회사·연도·상태·원천 식별자 |
| 승인 중복 방지 | 회차당 승인 한 건, 승인당 취소 한 건 |
| 원장 전기·역분개 | 결정·조정 행 UQ, 역분개 대상 부분 UQ |
| 원장 집계 | 회사·연도·자산·유보 항목 |
| 감사 조회 | 회사·원천 버전, 회사·종류·키·기록 시각 |

원장·결산 금액 합계, 조정행 방향·처분, 이전 확정 잔액과 차기 기초, 정책 적용·확정 잠금은 승인·결산 트랜잭션에서도 재검사한다. 조회 인덱스는 검증을 대신하지 않는다.

## I. 기존 자료와의 대응

| 현재 자료 | 제안 대상·주의 |
|---|---|
| `workspace.payload.master` | stock_master. 코드·명칭만으로 불확실한 종목을 자동 병합하지 않음 |
| assets / trades / valuations | investment_assets / trade_events / valuation_events, 종류별 기존 ID 매핑 |
| taxPolicy / taxPolicyYears | tax_cost_policy_versions, 공통 기준·신고 정보 보존 |
| fvociPolicy / fvociPolicyYears | fvoci_policy_versions, 기존 기준과 연도별 이력 보존 |
| stockPolicies | investment_assets의 회계 원가 정책 및 계산 입력 스냅샷 |
| taxAdjustmentScope | company_rule_reviews, 미확인을 확인 완료로 바꾸지 않음 |
| taxBasis 및 기타 호환 설정 | 원문·입력 스냅샷 보존. 별도 세무원가라면 개별 매핑·대사 전 이전 완료 처리 금지 |
| adjustments | legacy_manual_adjustments. 새 승인원장과 구분 |
| 기존 validation_runs·issue 검토 | 계산 연결이 있으면 대응시키고, 없는 근거는 LEGACY 원문으로 보존 |
| 기존 tax_workpapers·승인·취소 이력 | 검증표 회차·조정행·결정·이벤트 및 원문 보관 |
| reserve_entries | 기존 ID·source_ref·부호·금액 보존, 승인·취소 연결 대사 |
| fiscal_closes·closing_events | 금액·시각·확정자·원문 근거 보존. 새 규칙으로 재확정하지 않음 |
| 기존 audit_log | 원문 보존 및 새 회사 경계에 매핑. 과거 로그를 삭제하거나 인증 이력으로 승격하지 않음 |

## J. 전환 전 필수 검증

1. 원본 DB·JSON 백업과 버전·해시를 확보하고 복구를 시험한다.
2. 원천 종류별 건수·논리 ID·버전·날짜·수량·금액·누락 항목을 비교한다.
3. 원천 특정 버전부터 계산·검증표·조정행·승인·원장·결산까지 참조가 이어지는지 확인한다.
4. 자산별 회계장부·세무원가·보유분 OCI·과거 처분 유지분 OCI·유보 및 종목 통합원가를 비교한다.
5. 평가·매수 배분·부분 처분·전량 처분·취소·재승인·다음 연도 이월 사례를 재현한다.
6. 확정 결산은 단순 재계산 합계뿐 아니라 기존 원문·정책 근거·원장 항목별 잔액·승인 상태·취소 관계까지 비교한다.
7. 외래키 무결성, 중복 원천, 다른 회사·연도 연결, 동시 저장 충돌, 실패 시 롤백을 검사한다.
8. LEGACY 자료는 원문 보존과 새 기능의 검증 성공 여부를 구분한다. 재현 불가능한 항목은 REVIEW_REQUIRED이며 전환을 중단한다.

이전 과정에서는 원 단위 처리, 세무조정 규칙, 정책 의미를 동시에 바꾸지 않는다. 이 문서 다음 단계는 설계 검토이며 실제 마이그레이션·운영 DB 전환은 별도 요청 후 수행한다.
