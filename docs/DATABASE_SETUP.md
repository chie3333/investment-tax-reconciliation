# 정규화 DB 생성·앱 연결

작성일: 2026-10-26 · 스키마 버전: 2

## 실행과 연결 확인

프로젝트 루트에서 실행한다.

```bash
python3 -m app.server
```

`http://127.0.0.1:8000/`에서 사용한다. 기본 DB는 `data/investment.sqlite3`이며 **파일이 없으면 새 정규화 DB를 생성**한다. 데이터 관리 화면에 `SQLite(종목·거래·평가 분리)`가 표시된다. HTML 파일만 직접 열면 서버 DB에 연결되지 않는다.

다른 새 파일을 선택하려면 다음처럼 실행한다.

```bash
python3 -m app.database create --path data/new_workspace.sqlite3
INVESTMENT_DB_PATH=data/new_workspace.sqlite3 python3 -m app.server
python3 -m app.database inspect --path data/new_workspace.sqlite3
```

`create`·`inspect` 기본 대상도 `data/investment.sqlite3`이다. `create`는 기존 파일이면 빈 파일이어도 거절한다. 임시 파일에서 생성·무결성 확인 후 기존 파일을 덮어쓰지 않는 방식으로 게시한다. `inspect`는 읽기 전용이고 없는 파일을 만들지 않는다.

기존 JSON 형식 DB가 있으면 앱은 **기존 저장 경로를 계속 사용**한다. 테이블 이름이 겹치는 두 구조를 한 파일에 혼합하지 않는다. 최초 생성 단계에서 만든 버전 1의 `investment_v2.sqlite3`가 남아 있어도 수정·삭제하지 않으며, 현재 앱은 버전 2 신규 파일을 사용한다. 이전 버전을 앱 경로로 지정하면 오류로 거절한다. 기존 DB 자동 이전 도구는 아직 없다.

## 실제 연결한 범위

| 화면·요청 | 신규 DB의 저장·조회 |
|---|---|
| 종목·자산 | `stock_master`, `investment_assets` |
| 거래·평가 | `trade_events`, `valuation_events`; 정정·삭제는 새 ACTIVE/VOID 버전 추가 |
| 사업연도·정책 | `fiscal_years`, 두 정책 버전 테이블, `company_rule_reviews` |
| 서버 독립 재계산 | `calculation_runs`, `calculated_balances`, `calculated_stock_pools` |
| 검증·이슈 검토 | `validation_runs`, `issue_reviews`, `issue_review_events` |
| 검증표·복수 조정 | `tax_workpapers`, `tax_adjustment_rows`, `workpaper_events` |
| 승인·취소 | `workpaper_decisions`; 원본 승인과 조정 행 보존 |
| 유보 전기·역분개 | `reserve_entries`; 승인·조정 행·취소 원본에 FK 연결 |
| 결산·차기 이월 | `fiscal_closes`, `closing_events`; 확정 근거와 정책·잔액 고정 |
| 변경 이력 | 원천·설정별 `audit_log` |

기존 화면의 HTTP 형식은 유지한다. 서버는 SQL 원천 열에서 기존 계산 입력을 재구성하며, 전체 작업공간을 `workspace.payload`에 중복 저장하지 않는다. 입력 당시 추가 정보는 개별 원천의 증빙 JSON에 보존한다. 회사 `settings_json`에는 화면 순서·추가 설정·기존 수동 세무원가 참고값 등을 보관하고 거래·평가 원천 배열은 넣지 않는다. 계산 실행·승인·결산의 불변 JSON은 당시 근거를 보존하는 스냅샷이다.

저장 요청의 버전 비교·원천 정정·감사 로그는 한 트랜잭션이다. 잘못된 입력이나 충돌이 있으면 전부 취소한다. 승인 때에는 최신 원천 버전·독립 재계산·법령 적용 확인·조정 방향·과세소득·자산별 유보를 검사한다. 결산 때에는 현재 검증·필수 승인·실제 조정 행·실제 전기·기초 이월·기말 잔액·종목 원가 풀을 다시 대사한다. 승인 취소는 원본 삭제 대신 반대 금액의 역분개를 추가한다.

과거 확정 원천·정책·결산은 수정하지 않는다. 미래 FVOCI 정책은 새 버전으로 추가하며 과거 처분을 재작성하지 않는다. 기간 자료가 생긴 뒤의 결산월 변경은 별도 기간 재편이 필요하여 차단한다. 최초 입력 전에 결산월을 설정한다.

## 보존과 현재 제한

- 업무 테이블 28개와 관리 테이블 `schema_metadata`를 생성한다. 처음에는 메타데이터와 기존 계산 식별용 `LEGACY_CENT_V1` 프로필만 있고 업무 자료는 0행이다.
- 화면 최초 접속은 빈 로컬 작업공간의 설정·감사 로그를 저장한다. 회사 이름은 미확인 로컬 작업공간으로 표시하고 사업자번호나 예제 거래를 만들지 않는다.
- 브라우저 데이터가 있으면 기존 가져오기 선택을 유지한다. 원본 DB·브라우저 데이터가 비었다고 추정해서 덮어쓰지 않는다.
- 기존 수동 조정은 원문 이력과 `legacy_manual_adjustments`에 보존하고 승인·유보 전기로 자동 전환하지 않는다.
- 금액은 정확한 십진 TEXT로 저장하고 합산은 Decimal로 한다. 기존 0.01원 계산 의미를 보존하며 원 단위 신고 반올림 규칙을 새로 확정한 작업은 아니다.
- 현재 앱은 로컬 작업공간 하나를 회사 1개에 연결한다. 여러 회사 선택·사용자 인증·역할 분리·운영 결산 재개방·기존 DB 이전은 미구현이다.
- 종목 CSV 입력과 평가 저장은 SQL에 반영한다. KRX 조회의 영구 `market_prices` 캐시·시세 정정·평가의 시세 FK 선택 화면은 아직 연결하지 않았다. 조회 결과 전체를 자동 저장하지 않는다.
- DB 파일은 `data/`에만 보관하고 Git에는 생성 코드·DDL·테스트·문서만 올린다. 파일 소유자의 직접 변조를 막는 암호학적 서명은 제공하지 않는다.

## 검증과 구현 위치

```bash
python3 -m unittest discover -s tests/python -t . -v
```

`tests/python/test_database_v2.py`는 28개 테이블·외래키·정밀 금액·원천 버전·확정 잠금·불변 사실·파일 보존을 검사한다. `test_database_app.py`는 실제 HTTP 핸들러를 사용해 정답 사례 16개 × FVOCI 정책 2개를 저장→승인→원장→결산까지 검증하고, 취소·재승인·저장 충돌·원자적 실패·연도 정책·3월 결산 이월·이슈 이력을 확인한다. 테스트 데이터는 모두 임시 DB에만 기록한다.

DDL은 `app/database/schema.sql`, 생성·검사·트리거는 `bootstrap.py`, 원천 저장 어댑터는 `repository.py`, 검증·승인·결산은 `services.py`에 있다. 기존 서버 서비스는 DB 형식에 따라 적절한 구현을 선택한다.

정의서에 추가된 구현 열은 `companies.settings_json`, 자산·FVOCI 정책·규칙 검토의 `metadata_json`, `validation_runs.result_json`, 수동 조정의 `is_current/status`, 그리고 회사·연도 연결을 강제하는 이슈 이벤트의 `fiscal_year_id`·유보원장의 `workpaper_id`다. 앱 연결에 필요한 원문·상태·외래키 근거를 위한 것이며 기존 DB의 현장 변경은 아니다.
