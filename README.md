# Steel Challenge Analytics Platform

여러 참가자가 보낸 Steel Challenge EAF 결과 이메일과 기존 DOCX 결과를 한 데이터 모델로 수집하고, 승인된 팀 구성원만 조회·시각화·다운로드·ML 분석할 수 있게 하는 Streamlit/Supabase 플랫폼입니다.

worldsteel의 공식 steelChallenge는 Electric Arc Furnace 시뮬레이터를 사용해 제한 조건 아래 생산 전략을 반복 개선하는 경연입니다. 이 프로젝트도 단일 결과의 순위만 보여주기보다 Run 간 비용·시간·공정 이벤트를 비교하도록 설계했습니다. ML과 Active Learning 결과는 관찰 데이터 기반의 탐색 보조이며 실제 최적 조업 조건으로 단정하지 않습니다. 참고: [worldsteel steelChallenge-20 발표](https://worldsteel.org/media/press-releases/2026/steelchallenge-20-world-champions-announcement/)

## 1. Architecture

```text
Steel Challenge email
  -> Gmail API (OAuth 2.0, gmail.modify)
  -> GitHub Actions (약 5분 schedule / 수동 실행)
  -> email_ingest.py
  -> email_parser.py (HTML 우선, plain text fallback)
  -> save_run_with_logs() transaction
  -> Supabase PostgreSQL: email_messages / runs / logs

DOCX manual upload
  -> parser.py
  -> same canonical data + same transaction

Browser
  -> Streamlit
  -> Supabase Auth email/password (session별 client/JWT)
  -> profiles 승인/권한 + PostgreSQL RLS
  -> Dashboard / Run detail / Plotly / ML / Active Learning / Excel
```

주요 보안 경계는 두 겹입니다.

1. Streamlit은 로그인 후 `approved = true AND can_view = true`를 확인합니다.
2. Supabase RLS가 익명 접근을 차단하고 동일한 권한을 DB에서 다시 검사합니다.

`sender_email`은 메일 발신자이고 `steel_user_id`는 본문의 Steel Challenge User ID입니다. 두 값은 합치지 않습니다. 기본 정렬은 Gmail 수신 시간이 아니라 본문 `Date`를 Asia/Seoul로 해석한 `run_date DESC`입니다.

팀 프로젝트에서는 다음처럼 역할을 나누는 편이 안전합니다.

- 플랫폼 관리자: Auth 사용자 승인 및 다운로드 권한 관리
- 데이터 담당: Gmail 실패/중복/Data Quality 점검
- 공정 담당: Event Log와 조성 범위의 공학적 해석
- 분석 담당: ML 누출 검토, 지표 해석, 후보 실험 검증

## 2. Supabase setup

1. [Supabase](https://supabase.com/)에서 새 프로젝트를 만듭니다.
2. Project Settings → API에서 Project URL, anon key, service role key를 확인합니다.
3. service role key는 GitHub Actions와 선택적인 관리자 초대 기능에만 사용합니다.
4. 기존 prototype DB가 있다면 SQL 적용 전에 Supabase backup 또는 `pg_dump`를 만드세요.

테이블 역할:

- `profiles`: Auth 사용자와 승인/조회/다운로드/관리자 권한
- `email_messages`: Gmail envelope, 파싱 상태, 오류, 중복 횟수
- `runs`: 검색·집계용 정규 컬럼과 호환용 `data_json`
- `logs`: Run별 순서가 보존된 Event Log

## 3. SQL 실행 방법

Supabase Dashboard → SQL Editor에서 다음 파일의 전체 내용을 순서대로 실행합니다.

1. [`supabase/schema.sql`](supabase/schema.sql)
2. [`supabase/rls.sql`](supabase/rls.sql)

`schema.sql`은 새 설치와 기존 prototype 업그레이드를 모두 고려합니다. legacy `data_json`의 주요 값을 직접 컬럼으로 backfill하고, Run과 Log를 하나의 트랜잭션으로 저장하는 RPC를 만듭니다.

`rls.sql`은 이 앱 전용 네 테이블의 기존 policy를 제거한 뒤 authoritative policy를 다시 만듭니다. 다른 앱이 같은 테이블을 공유하고 있다면 먼저 policy를 검토하세요.

SQL을 수정했다면 최소한 다음을 다시 확인합니다.

- anon 역할은 `profiles`, `runs`, `logs`, `email_messages`를 읽지 못함
- 승인되지 않은 authenticated 사용자는 Run을 읽지 못함
- 승인된 viewer는 Run/Log만 읽을 수 있음
- admin만 모든 profile 및 email quality 데이터를 읽고 profile 권한을 변경함
- Gmail worker의 service role은 수집을 수행할 수 있음

## 4. Supabase Auth 설정

1. Authentication → Providers → Email을 활성화합니다.
2. 공개 회원가입은 앱에서 제공하지 않습니다. 필요하면 Supabase의 신규 가입 허용 옵션도 끕니다.
3. Authentication → Users에서 첫 사용자를 직접 생성합니다.
4. 이후 관리자는 앱의 `사용자 관리`에서 초대할 수 있습니다. 이 기능은 Streamlit 서버에 `SUPABASE_SERVICE_ROLE_KEY`가 있을 때만 보입니다.

앱은 `sign_in_with_password()`를 사용하며 access/refresh token을 각 Streamlit `session_state`에 따로 보관합니다. 전역 authenticated client는 없습니다.

## 5. 첫 admin 생성

첫 Auth 사용자를 만든 뒤 SQL Editor에서 이메일을 바꿔 아래 SQL을 한 번 실행합니다.

```sql
update public.profiles
set approved = true,
    can_view = true,
    can_download = true,
    is_admin = true,
    updated_at = now()
where email = 'YOUR_ADMIN_EMAIL@example.com';
```

확인:

```sql
select email, approved, can_view, can_download, is_admin
from public.profiles
order by created_at;
```

일반 관리자 화면에서는 `is_admin`을 변경할 수 없습니다. 두 번째 최고 관리자가 필요하면 SQL Editor에서 신중하게 설정하세요.

## 6. Gmail OAuth 설정

Gmail API는 IMAP/app password가 아닌 공식 OAuth 2.0을 사용합니다. Google의 [Gmail API 시작 안내](https://developers.google.com/workspace/gmail/api/auth/web-server)와 [scope 안내](https://developers.google.com/workspace/gmail/api/auth/scopes)를 함께 확인하세요.

1. [Google Cloud Console](https://console.cloud.google.com/)에서 프로젝트를 만듭니다.
2. APIs & Services → Library에서 Gmail API를 활성화합니다.
3. OAuth consent screen을 구성합니다. 테스트 상태라면 Gmail 수신 계정을 Test user로 추가합니다.
4. Credentials → Create credentials → OAuth client ID → Desktop app을 선택합니다.
5. Client ID와 Client Secret을 안전한 비밀번호 관리자에 보관합니다.
6. 토큰을 발급할 Google 계정은 `GMAIL_TARGET_ADDRESS`의 메일을 읽을 수 있어야 합니다.

사용 scope는 `https://www.googleapis.com/auth/gmail.modify`입니다. 메시지 본문을 읽고 처리 완료 label을 붙이기 위해 필요합니다. 앱은 메일을 삭제하거나 보내지 않습니다.

## 7. Gmail refresh token 생성

의존성을 설치한 로컬 PC에서만 실행하세요. Google Cloud에서 다운로드한
Desktop OAuth JSON은 저장소 밖에 두고 그 경로를 지정하면 됩니다.

```powershell
py scripts/generate_gmail_refresh_token.py --credentials "C:\Users\Owner\Downloads\client_secret_....json"
```

`--credentials`를 생략하면 Client ID와 Client Secret을 직접 묻습니다. Client Secret
입력값은 터미널에 표시되지 않습니다.

브라우저에서 대상 Gmail 계정으로 동의하면 helper가 refresh token을 한 번 출력합니다. 즉시 GitHub Secret `GMAIL_REFRESH_TOKEN`에 넣고 터미널 기록/스크린샷/채팅에 공유하지 마세요. 스크립트는 토큰 파일을 만들지 않습니다.

토큰이 나오지 않으면 Google 계정의 기존 앱 권한을 철회한 뒤 다시 `prompt=consent` 흐름을 실행합니다.

## 8. GitHub Secrets

Repository → Settings → Secrets and variables → Actions에서 다음 Repository secrets를 만듭니다.

| Secret | 내용 |
|---|---|
| `SUPABASE_URL` | Supabase Project URL |
| `SUPABASE_KEY` | Supabase anon key; 호환 설정용 |
| `SUPABASE_SERVICE_ROLE_KEY` | Gmail worker 전용 service role key |
| `GMAIL_CLIENT_ID` | Google OAuth client ID |
| `GMAIL_CLIENT_SECRET` | Google OAuth client secret |
| `GMAIL_REFRESH_TOKEN` | 앞 단계에서 발급한 refresh token |
| `GMAIL_TARGET_ADDRESS` | 결과 메일이 도착하는 주소 |

실제 값은 `.env`, YAML, Python, Issue, Actions 출력에 넣지 않습니다.

## 9. Streamlit Secrets

로컬에서는 예제 파일을 복사합니다.

```powershell
Copy-Item .streamlit/secrets.toml.example .streamlit/secrets.toml
```

필수 값:

```toml
SUPABASE_URL = "https://YOUR_PROJECT.supabase.co"
SUPABASE_ANON_KEY = "YOUR_ANON_KEY"
```

관리자 Auth 초대를 앱에서 사용할 때만 아래 값을 서버 secrets에 추가합니다.

```toml
SUPABASE_SERVICE_ROLE_KEY = "YOUR_SERVICE_ROLE_KEY"
```

`SUPABASE_SERVICE_ROLE_KEY`는 브라우저 JavaScript나 공개 저장소에 절대 노출하지 않습니다. `.streamlit/secrets.toml`은 `.gitignore`에 포함되어 있습니다.

## 10. GitHub Actions

워크플로는 [`.github/workflows/email_ingest.yml`](.github/workflows/email_ingest.yml)에 있습니다.

- `workflow_dispatch`: Actions 화면에서 수동 시험
- `*/5 * * * *`: 약 5분 간격 예약 실행
- `concurrency`: 이전 수집과 겹치는 동시 실행 방지
- 개별 메시지 파싱 실패: `email_messages.parsed_status = failed`로 기록하고 다음 메시지 계속 처리
- worker 자체 설정/Gmail/DB 초기화 실패: job 실패

GitHub schedule은 부하 상황에 따라 지연될 수 있으며 정확한 실시간 실행을 보장하지 않습니다.

첫 시험:

1. 결과 이메일 한 개를 대상 Gmail로 보냅니다.
2. GitHub → Actions → Steel Challenge Gmail ingestion → Run workflow를 누릅니다.
3. Supabase `email_messages`, `runs`, `logs`를 확인합니다.
4. Gmail 메시지에 `SteelChallengeProcessed` label이 붙었는지 확인합니다.
5. 같은 workflow를 다시 실행해 Run이 중복되지 않는지 확인합니다.

## 11. Local run

Python 3.11 이상을 권장합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .streamlit/secrets.toml.example .streamlit/secrets.toml
streamlit run app.py
```

테스트와 독립 실행 확인:

```powershell
pytest -q
python email_ingest.py --help
python email_ingest.py --max-results 10
```

마지막 명령은 Gmail/Supabase 환경변수가 모두 있을 때만 실행합니다.

## 12. Deployment

Streamlit Community Cloud 기준:

1. 이 저장소를 GitHub에 push합니다.
2. Streamlit Cloud에서 New app을 선택하고 entry point를 `app.py`로 지정합니다.
3. App settings → Secrets에 9절의 값을 넣습니다.
4. 배포 후 로그인 화면만 공개 상태에서 보이는지 확인합니다.
5. 승인되지 않은 계정, viewer, downloader, admin 네 권한 조합을 각각 시험합니다.
6. GitHub Actions는 Streamlit과 별도로 Gmail을 수집하므로 Streamlit 프로세스에 polling loop가 없습니다.

다른 호스팅에서도 환경변수 우선, `st.secrets` fallback으로 동일하게 동작합니다.

## 13. Security notes

- anon key는 공개 client 식별자이지만 RLS가 반드시 적용되어야 합니다.
- service role key는 RLS를 우회합니다. GitHub Actions/서버 secrets 외에는 두지 마세요.
- 다운로드 메뉴 숨김만 믿지 않고 Excel 생성 직전 profile 권한을 다시 읽습니다.
- 관리자 메뉴도 UI 표시와 별도로 backend에서 `is_admin`을 다시 확인합니다.
- 앱에 회원가입, 하드코딩 관리자 비밀번호, 운영 DB 전체 삭제 버튼이 없습니다.
- Excel export는 제어문자를 제거하고 `=`, `+`, `-`, `@`로 시작하는 문자열을 escape합니다.
- Gmail `gmail_message_id`와 `runs.email_message_id`는 unique하여 중복 Run을 차단합니다.
- Event Log는 `log_no`를 보존하고 `event_seconds`를 별도 저장합니다.
- Status 0/1의 의미를 임의로 성공/실패로 표시하지 않습니다.
- ML은 숫자 feature만 사용하고 목표별 직접 누출 feature를 제외하며, 20개 미만이면 학습하지 않습니다.
- Active Learning 후보는 관측된 controllable feature의 min/max 안에서만 생성되지만 실제 조업 안전성을 보증하지 않습니다.
- 앱 Excel 상한은 Runs 10,000건, Logs 100,000건입니다. 그 이상은 Supabase의 관리형 export를 사용하세요.

Supabase는 Auth JWT와 RLS를 함께 사용하는 구조를 권장합니다. 참고: [Supabase Auth](https://supabase.com/docs/guides/auth), [Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security). Gmail 처리 label은 공식 `users.messages.modify` API를 사용합니다: [Gmail messages.modify](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/modify).

## Project files

- `app.py`, `ui_components.py`: 인증 게이트와 화면 라우터
- `parser.py`, `email_parser.py`, `canonical.py`: DOCX/HTML/plain canonical parser
- `gmail_client.py`, `email_ingest.py`: Gmail API와 예약 worker
- `db_utils.py`, `auth_utils.py`, `config.py`: DB/Auth/secret 계층
- `feature_engineering.py`, `ml_engine.py`, `active_learning.py`: 공정 feature와 분석
- `visualization.py`, `excel_utils.py`: Plotly 및 3-sheet Excel
- `supabase/schema.sql`, `supabase/rls.sql`: DB migration과 권한
- `tests/`: parser, DOCX 회귀, feature, ML, Excel 테스트
