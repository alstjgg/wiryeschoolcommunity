# UI 수정 요청 — 5개 이슈

- 수정 대상 파일은 public/theme.json, .chainlit/config.toml, public/stylesheet.css, chainlit.md 중 해당하는 파일.

---

## 이슈 1: 첫 화면 중앙 제목이 "Chainlit"으로 표시됨

**현재 상태**: 챗봇 첫 화면 중앙에 큰 글씨로 "Chainlit"이라고 보임
**기대 상태**: "위례인생학교 업무 도우미"로 변경

**수정 방법**:
- `.chainlit/config.toml`의 `[UI]` 섹션에서 `name = "위례인생학교 업무 도우미"` 확인 (이미 설정되어 있다면 적용이 안 된 것이므로 chainlit.md 파일을 확인)
- `chainlit.md` 파일 내용이 첫 화면 웰컴 텍스트로 표시될 수 있음 — 이 파일에 "위례인생학교 업무 도우미"를 포함하도록 수정
- 브라우저 탭 제목도 변경: config.toml의 `[project]` 섹션에 `name = "위례인생학교 업무 도우미"` 확인

---

## 이슈 2: Dark mode 전환이 가능하고, 전환 시 UI가 깨짐

**현재 상태**: 헤더 우측 상단에 다크모드 토글 아이콘이 있음. 전환하면 상자 테두리가 하얀색으로 바뀌고 화면이 밝게 변하는 등 문제 발생
**기대 상태**: Dark mode 전환 버튼 자체를 숨기거나, 전환 불가능하게 하기. Light mode 고정.

**수정 방법**:
- `.chainlit/config.toml`의 `[UI]` 섹션에서 `default_theme = "light"` 확인
- 다크모드 토글 버튼을 숨기기 위해 `public/stylesheet.css`에 추가:

```css
/* 다크모드 토글 버튼 숨기기 */
/* Web Inspector로 정확한 선택자를 확인한 뒤 적용할 것 */
/* 일반적으로 아래와 같은 형태: */
button[aria-label*="theme"],
button[aria-label*="dark"],
button[aria-label*="light"],
button[aria-label*="Theme"] {
  display: none !important;
}
```

참고: 정확한 선택자를 찾으려면 배포된 사이트에서 F12 → 다크모드 토글 버튼을 Inspect → 해당 요소의 클래스명이나 aria-label 확인 필요.

---

## 이슈 3: Starter 버튼과 Action 버튼의 테두리가 안 보임

**현재 상태**: 버튼 테두리가 하얀색이라 밝은 배경(#FAF6EF) 위에서 버튼 영역이 보이지 않음. 마우스 호버해야 색이 바뀌면서 버튼 형태가 드러남.
**기대 상태**: 호버 전에도 버튼 영역이 명확히 보여야 함. border 색상을 #DED7CA(확정된 border 색상)로 적용.

**수정 방법**:
- `public/theme.json`에서 `--border` 값이 `"36 24% 83%"` (#DED7CA)로 설정되어 있는지 확인
- theme.json만으로 안 되면 `public/stylesheet.css`에 추가:

```css
/* Starter 버튼 — Web Inspector로 정확한 선택자 확인 필요 */
/* Conversation Starter 버튼 테두리 */
[class*="starter"] button,
[class*="Starter"] button {
  border: 1px solid #DED7CA !important;
  background: #FEFCF8 !important;
}

/* Action 버튼 테두리 */
[class*="action"] button,
[class*="Action"] button {
  border: 1px solid #DED7CA !important;
}
```

참고: 정확한 선택자는 Web Inspector로 Starter 버튼/Action 버튼을 Inspect해서 확인할 것.

---

## 이슈 4: 사용자 메시지 버블 색상이 잘못됨

**현재 상태**: 사용자가 보낸 메시지의 배경색이 secondary(#EAE2D4) 같은 연한 색이고, 텍스트가 검은색
**기대 상태**: 사용자 메시지 배경 = primary (#7A5234), 텍스트 = 흰색 (#FFFFFF)

**수정 방법**:
- Chainlit의 사용자 메시지 버블은 theme.json의 `--primary`와 `--primary-foreground`를 사용해야 함
- theme.json에서 값 확인:
  - `"--primary": "26 40% 34%"` (#7A5234)
  - `"--primary-foreground": "0 0% 100%"` (#FFFFFF)
- 만약 theme.json이 정확한데도 적용이 안 된다면, custom CSS로 강제 적용:

```css
/* 사용자 메시지 버블 — Web Inspector로 정확한 선택자 확인 필요 */
/* 보통 data-* 속성이나 특정 클래스로 구분됨 */
[data-role="user"] .message-content,
.user-message {
  background-color: #7A5234 !important;
  color: #FFFFFF !important;
}
```

참고: Chainlit이 사용자/봇 메시지를 어떤 클래스나 속성으로 구분하는지 Web Inspector로 확인 필수.

---

## 이슈 5: Accent 색상(#2B7A6E 틸)이 UI에서 전혀 사용되지 않음

**현재 상태**: accent 색상이 어디에도 보이지 않음
**기대 상태**: accent 색상이 의미 있는 곳에 사용되어야 함

**제안하는 적용 위치**:
- Action 버튼 중 "다음 추천 작업" 버튼의 배경색으로 사용 (primary와 구분)
- Step 진행 중 표시의 포인트 색상
- 사이드바에서 현재 활성 대화 하이라이트
- 링크 텍스트 색상

이 중 일부는 Chainlit이 accent 변수를 자동으로 사용하는 곳이 있을 수 있고, 없다면 custom CSS로 적용.
theme.json의 `--accent` 값이 `"171 48% 32%"` (#2B7A6E)로 설정되어 있는지 먼저 확인.

---

## 공통 참고사항

- Chainlit은 동적 CSS 클래스명을 사용하므로, 위에 적은 선택자는 예시임
- 각 이슈 수정 전에 반드시 배포된 사이트에서 Web Inspector(F12)로 해당 요소를 Inspect하여 정확한 선택자를 확인할 것
- `!important`는 최후 수단 — theme.json 변수로 해결 가능하면 그쪽 우선
- 수정 후 반드시 브라우저 캐시를 완전 삭제(Cmd+Shift+R)하고 확인할 것