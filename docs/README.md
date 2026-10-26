# 문서 안내

## DB 보완 설계·생성·앱 연결

- [보완 ERD](DATABASE_ERD.md): 기존 ERD에서 보완할 관계와 자료 흐름, 시세·정책·승인·결산 보존 원칙
- [데이터 흐름도](DATABASE_DATA_FLOW.md): 수집·입력·계산·승인·취소·결산·이월 및 향후 DB 이전 흐름
- [테이블 정의서](DATABASE_TABLES.md): 제안 28개 테이블의 필드·형식·연결·제약·인덱스·이전 대사 기준
- [새 DB 생성·검사 안내](DATABASE_SETUP.md): 신규 DB 앱 연결, 기존 파일 보존, 구현 범위와 실행 방법

정규화 스키마 v2와 앱 연결을 구현했다. 신규 파일은 각 원천 테이블에서 읽고 저장하며 승인·원장·결산까지 연결된다. 기존 JSON DB는 원래 방식으로 유지하고 자동 이전하지 않는다. 원 단위 처리 기준은 변경하지 않았다.

## 현재 프로그램 사용·검증 문서

- [업무 흐름](WORKFLOW.md)
- [서버 독립 재계산·승인·결산 연결](SERVER_RECALCULATION.md)
- [FVOCI 계산 정답표](FVOCI_ANSWER_CASES.md)
- [FVOCI 부분 처분 유보 환입 대사](FVOCI_RESERVE_RECONCILIATION.md)
- [2026 세무조정 복수 행 규칙](TAX_ADJUSTMENT_RULES_2026.md)
- [FVOCI 사업연도별 정책·이월](FVOCI_POLICY_YEARS.md)
- [개발 이력](DEVELOPMENT_HISTORY.md)
