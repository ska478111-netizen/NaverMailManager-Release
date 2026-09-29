import sys, json, urllib.parse, urllib.request
from pathlib import Path
import importlib.util

base = Path(__file__).resolve().parents[1] / 'v3.0' / 'app.py'
if not base.exists():
    raise RuntimeError('v3.0 기반 파일을 찾을 수 없습니다: ' + str(base))
spec = importlib.util.spec_from_file_location('nmm_v30', str(base))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def diagnostic_send(self, text):
    token = self._kakao_access_token()
    template = {'object_type':'text','text':text[:1800],
                'link':{'web_url':'https://mail.naver.com','mobile_web_url':'https://mail.naver.com'}}
    form = urllib.parse.urlencode({'template_object':json.dumps(template,ensure_ascii=False)}).encode()
    req = urllib.request.Request(
        'https://kapi.kakao.com/v2/api/talk/memo/default/send',
        data=form,
        headers={'Authorization':'Bearer '+token,
                 'Content-Type':'application/x-www-form-urlencoded;charset=utf-8'})
    try:
        raw = urllib.request.urlopen(req,timeout=20).read().decode('utf-8','replace')
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode('utf-8','replace')
        except Exception:
            body = ''
        code = ''
        msg = ''
        try:
            detail = json.loads(body)
            code = detail.get('code', detail.get('error_code',''))
            msg = detail.get('msg', detail.get('error_description',''))
        except Exception:
            pass
        raise RuntimeError(
            f'Kakao API HTTP {e.code} {e.reason}\n'
            f'code: {code if code != "" else "(없음)"}\n'
            f'msg: {msg if msg else "(없음)"}\n'
            f'응답: {body[:1200] if body else "(응답 본문 없음)"}')
    try:
        out = json.loads(raw)
    except Exception:
        raise RuntimeError('카카오 응답 JSON 해석 실패: ' + raw[:1200])
    if out.get('result_code') != 0:
        raise RuntimeError('카카오 발송 실패: ' + json.dumps(out,ensure_ascii=False))
    return True

mod.App._kakao_send_text = diagnostic_send
_orig_init = mod.App.__init__
def init_v301(self, *a, **kw):
    _orig_init(self, *a, **kw)
    self.title('네이버 메일 중요선별 v3.0.1 카카오 오류진단')
mod.App.__init__ = init_v301

if __name__ == '__main__':
    mod.App().mainloop()
