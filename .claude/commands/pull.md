# 풀 — GitHub/GCP → 로컬 전체 동기화

GitHub와 GCP의 최신 데이터를 비교하여 로컬에 동기화합니다.

## 실행 순서

### 1단계: 로컬 상태 확인

```bash
git status
```

- 커밋되지 않은 변경사항이 있으면 경고 후 stash 제안
- 클린 상태면 진행

### 2단계: GitHub에서 풀

```bash
git fetch origin
git log --oneline HEAD..origin/master  # 새 커밋 확인
git pull myfork master
```

### 3단계: GCP에서 로컬 전용 파일 역동기화

GCP에서 변경되었을 수 있는 파일들을 로컬로 복사:

**메모리 파일 (GCP 모바일 세션에서 업데이트되었을 수 있음):**
```bash
# GCP 메모리 → 로컬 메모리 (비교 후 최신만)
gcloud compute ssh sanjuk-project --zone=us-central1-b --command="ls -la ~/.claude/projects/-home-ohmil-3dsmax-mcp/memory/ 2>/dev/null"
```

각 파일의 수정 시간을 비교:
```bash
# GCP 파일이 더 최신이면 복사
gcloud compute scp ohmil@sanjuk-project:/home/ohmil/.claude/projects/-home-ohmil-3dsmax-mcp/memory/* ~/.claude/projects/C--dev-3dsmax-mcp/memory/ --zone=us-central1-b
```

### 4단계: GCP VM 코드도 최신으로

```bash
gcloud compute ssh sanjuk-project --zone=us-central1-b --command="cd ~/3dsmax-mcp && git pull myfork master 2>&1"
```

### 5단계: 변경사항 비교 보고

동기화 완료 후 요약:
- GitHub: 새로 받은 커밋 목록
- GCP → 로컬: 업데이트된 메모리 파일
- 충돌: 있으면 표시 및 해결 제안
