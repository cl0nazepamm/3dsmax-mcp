# 푸시 — 로컬 → GitHub/GCP/모바일 전체 동기화

로컬의 모든 최신 데이터를 GitHub, GCP VM, 연결된 모바일까지 동기화합니다.

## 실행 순서

### 1단계: 로컬 변경사항 확인 및 커밋

```bash
git status
git diff --stat
```

- 변경된 파일이 있으면 자동 커밋 (conventional commit 형식)
- 민감 파일(.env, credentials 등) 제외
- 변경 없으면 이 단계 스킵

### 2단계: GitHub에 푸시

```bash
git push myfork master
```

### 3단계: GCP VM 코드 동기화

```bash
gcloud compute ssh sanjuk-project --zone=us-central1-b --command="cd ~/3dsmax-mcp && git pull origin master 2>&1"
# Note: GCP remote is set to kanzaka110/3dsmax-mcp (the fork)
```

### 4단계: GCP VM에 로컬 전용 파일 동기화

로컬에만 있는 파일들(gitignore 대상)을 GCP에 복사:

**스킬 파일 (빌드된 .claude/skills/):**
```bash
# skills/ 디렉토리의 최신 파일을 GCP에 동기화
gcloud compute scp --recurse skills/3dsmax-mcp-dev/ ohmil@sanjuk-project:/home/ohmil/3dsmax-mcp/skills/3dsmax-mcp-dev/ --zone=us-central1-b
```

**글로벌 rules:**
```bash
for f in ~/.claude/rules/common/*.md; do
  gcloud compute scp "$f" ohmil@sanjuk-project:/home/ohmil/.claude/rules/common/$(basename "$f") --zone=us-central1-b
done
```

**메모리 파일:**
```bash
# 로컬 메모리 → GCP 프로젝트 메모리
gcloud compute ssh sanjuk-project --zone=us-central1-b --command="mkdir -p ~/.claude/projects/-home-ohmil-3dsmax-mcp/memory"
for f in ~/.claude/projects/C--dev-3dsmax-mcp/memory/*; do
  gcloud compute scp "$f" ohmil@sanjuk-project:/home/ohmil/.claude/projects/-home-ohmil-3dsmax-mcp/memory/$(basename "$f") --zone=us-central1-b
done
```

### 5단계: GCP remote-control 세션 확인

```bash
gcloud compute ssh sanjuk-project --zone=us-central1-b --command="tmux capture-pane -t max3d -p 2>&1 | tail -5"
```

- remote-control이 죽었으면 재시작:
```bash
gcloud compute ssh sanjuk-project --zone=us-central1-b --command="tmux send-keys -t max3d 'claude remote-control --name 3dsmax-mcp-GCP' Enter"
```

### 6단계: 결과 보고

모든 단계 완료 후 요약:
- Git: 커밋/푸시 결과
- GCP: 동기화된 파일 수
- 모바일: remote-control 상태 (모바일은 GCP 동기화 시 자동 반영)
