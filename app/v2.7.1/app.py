import imaplib, email, sqlite3, os, re, json, ssl, threading
from email.header import decode_header
from email.utils import parsedate_to_datetime, parseaddr
from pathlib import Path
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

APP_DIR = Path.home() / 'NaverMailManagerV22'
DB_PATH = APP_DIR / 'mail_v2.db'
ATTACH_DIR = APP_DIR / 'attachments'
CONFIG_PATH = APP_DIR / 'config.json'
IMAP_HOST, IMAP_PORT = 'imap.naver.com', 993

WORK_WORDS = ['발주','견적','계약','납품','거래명세','세금계산서','invoice','quotation','purchase order','지원사업','사업공고','스마트공장','보조금','과제','논문','대학원','교수','회의','미팅','제안서','신청서','서류 제출','회신 요청','확인 요청']
ACTION_WORDS = ['회신','제출','납부','입금','승인','서명','확인 부탁','확인 요청','마감','기한','신청','예약 변경','결제 실패','인증','비밀번호 재설정']
PERSONAL_WORDS = ['보안','인증','비밀번호','로그인','개인정보','신용점수','카드 승인','은행','보험','연금','건강검진','병원 예약','학교','학부모','입시','합격','원서','배송 완료','택배','결제내역','영수증','고지서','납부','국세','지방세','홈택스','정부24']
AD_STRONG = ['[광고]','(광고)','수신거부','unsubscribe','광고성 정보','광고 수신','프로모션','기획전','타임세일','특가','쿠폰','무료체험','추천 상품','회원님만의 혜택']
AD_WEAK = ['할인','이벤트','쇼핑','뉴스레터','마케팅','포인트 소멸','혜택 안내','신상품','세일','체험단','클래스 추천']
URGENT_WORDS = ['긴급','urgent','오늘까지','금일','즉시','D-1','D-2','회신 요청','제출 기한','납부 기한']
MUST_ACTION_STRONG=['발주서','발주 요청','견적 요청','계약서','계약 체결','세금계산서','입금 요청','미수금','납부 기한','제출 기한','회신 요청','승인 요청','서명 요청','신청 마감','마감 임박','보안 경고','로그인 알림','비밀번호 재설정','인증번호','카드 승인','결제 실패','국세','지방세','고지서','합격','원서 접수','면접 일정']
INFO_DOWNGRADE=['이용약관','약관 개정','정책 개정','뉴스레터','아침시황','금일 시황','시황','칼럼','세미나 안내','교육 안내','수강 안내','명절 인사','추석 인사','설 인사','웹비나','포인트 소멸','회원 혜택','서비스 안내','기능 안내','공지사항']
HARD_PROTECT=['세금계산서','계약서','계약 체결','발주서','발주 요청','견적 요청','입금 요청','미수금','결제 실패','카드 승인','보안 경고','로그인 알림','비밀번호 재설정','인증번호','국세','지방세','고지서']
EXPLICIT_AD=['[광고]','(광고)','광고)','수신거부','무료수신거부','unsubscribe']


PROTECTED_DOMAINS = ['gov.kr','go.kr','nts.go.kr','hometax.go.kr','korea.kr','ac.kr']


def decode_mime(value):
    if not value: return ''
    out=[]
    for part, enc in decode_header(value):
        if isinstance(part, bytes):
            codecs=[]
            if enc: codecs.append(enc)
            codecs += ['utf-8','cp949','euc-kr','latin1']
            for codec in codecs:
                try: out.append(part.decode(codec)); break
                except Exception: pass
            else: out.append(part.decode('utf-8', errors='replace'))
        else: out.append(part)
    return ''.join(out)


def safe_name(name): return re.sub(r'[\\/:*?"<>|]+','_',name or 'attachment')[:180]


def extract_text(msg):
    texts=[]
    parts=msg.walk() if msg.is_multipart() else [msg]
    for p in parts:
        ctype=p.get_content_type(); disp=(p.get('Content-Disposition') or '').lower()
        if ctype not in ('text/plain','text/html') or 'attachment' in disp: continue
        payload=p.get_payload(decode=True)
        if not payload: continue
        charset=p.get_content_charset() or 'utf-8'
        try: s=payload.decode(charset, errors='replace')
        except Exception: s=payload.decode('utf-8', errors='replace')
        if ctype=='text/html':
            s=re.sub(r'<style.*?</style>|<script.*?</script>',' ',s,flags=re.I|re.S)
            s=re.sub(r'<[^>]+>',' ',s)
        texts.append(re.sub(r'\s+',' ',s).strip())
    return '\n'.join(texts)[:120000]


def sender_domain(sender):
    addr=parseaddr(sender)[1].lower()
    return addr.split('@')[-1] if '@' in addr else ''


def score_mail(subject, body, sender, has_attachment=False, trusted=False, ad_sender=False):
    text=(subject+' '+body[:10000]+' '+sender).lower(); subj=subject.lower(); domain=sender_domain(sender)
    def hits(words, weight_subject=2, weight_body=1):
        score=0; matched=[]
        for w in words:
            lw=w.lower()
            if lw in subj: score += weight_subject; matched.append(w)
            elif lw in text: score += weight_body; matched.append(w)
        return score, matched

    work, wm=hits(WORK_WORDS,3,1); personal, pm=hits(PERSONAL_WORDS,3,1); action, am=hits(ACTION_WORDS,2,1)
    strong, sm=hits(AD_STRONG,5,3); weak, wkm=hits(AD_WEAK,2,1); ad=strong+weak
    must_strong, msm=hits(MUST_ACTION_STRONG,6,2); info, im=hits(INFO_DOWNGRADE,4,1)
    hard, hm=hits(HARD_PROTECT,10,4); explicit_ad, eam=hits(EXPLICIT_AD,12,5)
    domain_protected=any(domain==d or domain.endswith('.'+d) for d in PROTECTED_DOMAINS)
    if has_attachment and work>0: work += 1

    # User's explicit sender protection always prevents quarantine.
    if trusted:
        bucket='필수숙지' if (hard>=10 or (must_strong>=6 and explicit_ad==0)) else '중요참고'
        priority='긴급' if bucket=='필수숙지' and any(x.lower() in text for x in URGENT_WORDS) else ('중요' if bucket=='필수숙지' else '일반')
        return bucket,priority,1 if bucket=='필수숙지' else 0,0,'사용자 보호 발신자',work,personal,ad

    # Explicit [광고]/unsubscribe wins over generic "deadline/application" language.
    # Only hard transaction/security/tax/order signals can rescue it.
    if explicit_ad>0 and hard==0:
        return '광고격리','일반',0,1,'명시적 광고/수신거부 표시 - 자동삭제 없음',work,personal,max(ad,explicit_ad)

    # Hard transaction/security/tax/order mail is must-read even if marketing wording coexists.
    if hard>=10:
        priority='긴급' if any(x.lower() in text for x in URGENT_WORDS) else '중요'
        return '필수숙지',priority,1,0,'실거래/세금/보안/발주 보호 신호',work,personal,ad

    # Generic action terms require stronger evidence and no explicit advertising.
    protected_action = must_strong>=6 or action>=4
    high_ad = strong>=8 or (strong>=5 and weak>=2) or (ad_sender and ad>=3)
    if high_ad and not protected_action and not domain_protected:
        return '광고격리','일반',0,1,f'고확신 광고 신호 {ad}점 - 자동삭제 없음',work,personal,ad

    if protected_action:
        priority='긴급' if any(x.lower() in text for x in URGENT_WORDS) else '중요'
        return '필수숙지',priority,1,0,'구체적 행동/기한 신호',work,personal,ad

    if domain_protected and (work>0 or personal>0):
        return '중요참고','일반',0,0,'기관/학교 도메인 - 참고 보존',work,personal,ad

    if info>=4:
        return '일반보관','일반',0,0,'정보성/약관/시황/교육 안내 - 필수숙지 제외',work,personal,ad

    if ad>0 or ad_sender:
        bucket='중요참고' if (work>=3 or personal>=3) else '일반보관'
        return bucket,'일반',0,0,f'광고 가능성 {ad}점 - 자동삭제 금지',work,personal,ad

    if work>=3 or personal>=3 or action>0:
        return '중요참고','일반',0,0,'업무/개인 관련 참고 신호',work,personal,ad

    return '일반보관','일반',0,0,'명확한 필수 행동 없음',work,personal,ad



def normalize_subject_pattern(subject):
    s=(subject or '').lower()
    s=re.sub(r'\[[^\]]{0,40}\]|\([^\)]{0,40}\)',' ',s)
    s=re.sub(r'\b(?:re|fw|fwd)\s*:\s*',' ',s,flags=re.I)
    s=re.sub(r'https?://\S+|\S+@\S+',' ',s)
    s=re.sub(r'\d{2,}','#',s)
    tokens=re.findall(r'[가-힣a-zA-Z]{2,}|#',s)
    stop={'안내','관련','메일','드립니다','입니다','대한','위한','그리고','the','and','for','with'}
    tokens=[x for x in tokens if x not in stop]
    return ' '.join(tokens[:8])[:160]

def learned_rule(con, subject, sender):
    key=parseaddr(sender or '')[1].lower()
    pat=normalize_subject_pattern(subject)
    pset=set(pat.split())
    rows=con.execute("SELECT sender_key,pattern,rule FROM learning_rules ORDER BY id DESC").fetchall()
    best=None; bestscore=-1
    for sk,rp,rule in rows:
        if sk and sk!=key: continue
        rset=set((rp or '').split())
        if not rset: continue
        overlap=len(pset & rset)
        threshold=1 if len(rset)<=2 else 2
        if overlap < threshold: continue
        score=(4 if sk==key and sk else 0)+overlap
        if score>bestscore:
            best=(rule,sk,rp); bestscore=score
    return best

def summary_text(subject, body):
    clean=re.sub(r'\s+',' ',body).strip()
    if not clean: return subject[:180]
    parts=re.split(r'(?<=[.!?。])\s+|(?<=다\.)\s+', clean)
    return ' '.join(parts[:3])[:450]


def init_db():
    APP_DIR.mkdir(exist_ok=True); ATTACH_DIR.mkdir(exist_ok=True)
    con=sqlite3.connect(DB_PATH, check_same_thread=False)
    con.execute('''CREATE TABLE IF NOT EXISTS mails(
      id INTEGER PRIMARY KEY, uid TEXT UNIQUE, msgid TEXT, sent_at TEXT, sender TEXT, subject TEXT,
      body TEXT, bucket TEXT, priority TEXT, must_read INTEGER DEFAULT 0, reviewed INTEGER DEFAULT 0,
      delete_candidate INTEGER DEFAULT 0, deleted_server INTEGER DEFAULT 0, reason TEXT, summary TEXT,
      attachments TEXT, work_score INTEGER DEFAULT 0, personal_score INTEGER DEFAULT 0, ad_score INTEGER DEFAULT 0)''')
    con.execute('CREATE INDEX IF NOT EXISTS idx_v2_date ON mails(sent_at)')
    con.execute('CREATE INDEX IF NOT EXISTS idx_v2_bucket ON mails(bucket)')
    con.execute('CREATE TABLE IF NOT EXISTS sender_rules(sender_key TEXT PRIMARY KEY, rule TEXT NOT NULL)')
    con.execute("""CREATE TABLE IF NOT EXISTS learning_rules(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      sender_key TEXT NOT NULL DEFAULT '',
      pattern TEXT NOT NULL DEFAULT '',
      rule TEXT NOT NULL,
      created_at TEXT NOT NULL,
      source_subject TEXT DEFAULT '',
      UNIQUE(sender_key,pattern,rule))""")
    con.commit(); return con


def load_config():
    if CONFIG_PATH.exists():
        try: return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        except Exception: pass
    return {}


def save_config(cfg):
    APP_DIR.mkdir(exist_ok=True); CONFIG_PATH.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding='utf-8')


class App(tk.Tk):
    def __init__(self):
        super().__init__(); self.title('네이버 메일 중요선별 v2.7.1 네이버 읽음상태 동기화'); self.geometry('1320x780')
        self.con=init_db(); self.cfg=load_config(); self.build(); self.refresh()

    def build(self):
        top=ttk.Frame(self); top.pack(fill='x',padx=10,pady=7)
        ttk.Label(top,text='네이버 ID/메일').pack(side='left')
        self.user=tk.StringVar(value=self.cfg.get('user','')); ttk.Entry(top,textvariable=self.user,width=27).pack(side='left',padx=5)
        ttk.Button(top,text='메일 수집·분석',command=self.collect).pack(side='left',padx=3)
        ttk.Button(top,text='저장된 1,000건 전체 재분류',command=self.reclassify_1000).pack(side='left',padx=3)
        ttk.Button(top,text='첨부폴더',command=lambda: os.startfile(ATTACH_DIR)).pack(side='left',padx=3)
        ttk.Label(top,text='안전모드: 자동 영구삭제 없음').pack(side='left',padx=14)
        self.status=tk.StringVar(value='준비'); ttk.Label(top,textvariable=self.status).pack(side='right')

        dash=ttk.Frame(self); dash.pack(fill='x',padx=10,pady=4)
        self.cards={}
        for key,title in [('must','🔴 필수숙지'),('important','🟠 중요참고'),('normal','⚪ 일반보관'),('ad','🗑 광고격리'),('deleted','서버 삭제')]:
            b=ttk.Button(dash,text=title,command=lambda k=key:self.set_filter(k)); b.pack(side='left',padx=3); self.cards[key]=b

        sf=ttk.Frame(self); sf.pack(fill='x',padx=10,pady=4)
        self.q=tk.StringVar(); e=ttk.Entry(sf,textvariable=self.q); e.pack(side='left',fill='x',expand=True); e.bind('<Return>',lambda _:self.refresh())
        ttk.Button(sf,text='검색',command=self.refresh).pack(side='left',padx=4); ttk.Button(sf,text='전체',command=lambda:self.set_filter('all')).pack(side='left')
        ttk.Button(sf,text='숙지 완료',command=self.mark_reviewed).pack(side='left',padx=5)
        ttk.Button(sf,text='네이버 읽음 동기화',command=self.sync_seen).pack(side='left',padx=3)
        ttk.Button(sf,text='집중 검토',command=self.focus_review).pack(side='left',padx=3)
        ttk.Button(sf,text='필수숙지 아님 학습',command=self.learn_not_must).pack(side='left',padx=3)
        ttk.Button(sf,text='필수숙지 지정 학습',command=self.learn_must).pack(side='left',padx=3)
        ttk.Button(sf,text='광고로 학습',command=self.learn_ad).pack(side='left',padx=3)
        ttk.Button(sf,text='학습 규칙 관리',command=self.manage_learning).pack(side='left',padx=3)
        ttk.Button(sf,text='검토 후 서버삭제',command=self.delete_selected).pack(side='left',padx=3)

        cols=('date','bucket','priority','review','sender','subject','reason','attach')
        self.tree=ttk.Treeview(self,columns=cols,show='headings',selectmode='extended')
        settings=[('date',125,'날짜'),('bucket',75,'구분'),('priority',60,'중요도'),('review',70,'숙지'),('sender',220,'보낸 사람'),('subject',400,'제목'),('reason',240,'판정 근거'),('attach',55,'첨부')]
        for c,w,t in settings: self.tree.heading(c,text=t); self.tree.column(c,width=w,anchor='w')
        self.tree.pack(fill='both',expand=True,padx=10,pady=6); self.tree.bind('<Double-1>',self.show_mail)
        self.filter='all'

    def sender_key_for_id(self, dbid):
        row=self.con.execute('SELECT sender FROM mails WHERE id=?',(dbid,)).fetchone()
        return parseaddr(row[0])[1].lower() if row else ''

    def _rule_match_count(self, sender_key, pattern, limit=1000):
        pset=set((pattern or '').split())
        if not pset: return 0
        threshold=1 if len(pset)<=2 else 2
        rows=self.con.execute("""SELECT sender,subject FROM mails WHERE deleted_server=0
                                 ORDER BY sent_at DESC LIMIT ?""",(limit,)).fetchall()
        n=0
        for sender,subject in rows:
            key=parseaddr(sender or '')[1].lower()
            if sender_key and key!=sender_key: continue
            cand=set(normalize_subject_pattern(subject or '').split())
            if len(pset & cand)>=threshold: n+=1
        return n

    def _learn_selected(self, rule):
        sel=self.tree.selection()
        if not sel:
            messagebox.showinfo('선택 필요','학습할 메일을 먼저 선택하세요.')
            return
        labels={'must':'필수숙지','not_must':'필수숙지 아님','ad':'광고'}
        prepared=[]; duplicate=0; conflicts=0; affected=set()
        for x in sel:
            row=self.con.execute('SELECT sender,subject FROM mails WHERE id=?',(x,)).fetchone()
            if not row: continue
            key=parseaddr(row[0] or '')[1].lower()
            pat=normalize_subject_pattern(row[1] or '')
            if not key or not pat: continue
            exact=self.con.execute(
                'SELECT rule FROM learning_rules WHERE sender_key=? AND pattern=?',(key,pat)).fetchone()
            if exact and exact[0]==rule:
                duplicate+=1; continue
            if exact and exact[0]!=rule: conflicts+=1
            prepared.append((key,pat,row[1] or ''))
            rows=self.con.execute("""SELECT id,sender,subject FROM mails WHERE deleted_server=0
                                     ORDER BY sent_at DESC LIMIT 1000""").fetchall()
            pset=set(pat.split()); threshold=1 if len(pset)<=2 else 2
            for mid,sender,subject in rows:
                if parseaddr(sender or '')[1].lower()!=key: continue
                if len(pset & set(normalize_subject_pattern(subject or '').split()))>=threshold:
                    affected.add(mid)
        if not prepared:
            messagebox.showinfo('중복 학습',
                f'선택한 규칙은 이미 학습되어 있습니다.\n'
                f'중복 {duplicate}건 — 전체 재분류를 생략합니다.')
            return
        preview=(f'새 학습 규칙: {len(prepared)}건\n'
                 f'이미 존재하는 중복: {duplicate}건\n'
                 f'기존 규칙과 충돌/변경: {conflicts}건\n'
                 f'최근 1,000건 중 유사 메일: {len(affected)}건\n\n'
                 f'선택한 메일을 "{labels[rule]}" 패턴으로 학습할까요?\n'
                 '발신자+제목 패턴을 사용하며 서버 메일은 삭제/이동하지 않습니다.')
        if not messagebox.askyesno('학습 영향 미리보기',preview):
            return
        before=self._bucket_counts()
        n=0
        for key,pat,source in prepared:
            self.con.execute(
                """INSERT OR REPLACE INTO learning_rules
                   (sender_key,pattern,rule,created_at,source_subject)
                   VALUES(?,?,?,?,?)""",
                (key,pat,rule,datetime.now().strftime('%Y-%m-%d %H:%M:%S'),source))
            n+=1
        self.con.commit()
        changed=self._apply_learning_to_saved()
        after=self._bucket_counts()
        self.refresh()
        delta=[]
        for k in ('필수숙지','중요참고','일반보관','광고격리'):
            d=after[k]-before[k]
            delta.append(f'{k}: {before[k]} → {after[k]} ({d:+d})')
        messagebox.showinfo('학습 완료',
            f'{n}건의 개인 맞춤 규칙을 저장했습니다.\n'
            f'중복 규칙 {duplicate}건은 저장/재처리하지 않았습니다.\n'
            f'기존 저장 메일 즉시 재분류: 실제 행 변경 {changed}건\n\n'
            + '\n'.join(delta) +
            '\n\n서버 메일 삭제/이동은 수행하지 않았습니다.')

    def _bucket_counts(self):
        return {b:self.con.execute(
            'SELECT count(*) FROM mails WHERE bucket=? AND deleted_server=0',(b,)).fetchone()[0]
            for b in ('필수숙지','중요참고','일반보관','광고격리')}

    def _apply_learning_to_saved(self, limit=1000):
        rows=self.con.execute("""SELECT id,subject,body,sender,attachments
                                 FROM mails WHERE deleted_server=0
                                 ORDER BY sent_at DESC LIMIT ?""",(limit,)).fetchall()
        changed=0
        for dbid,subj,body,sender,attachments in rows:
            bucket,prio,must,delcand,reason,ws,ps,ads=self.classify_with_learning(
                subj or '',body or '',sender or '',bool(attachments))
            old=self.con.execute('SELECT bucket,priority,must_read,delete_candidate FROM mails WHERE id=?',(dbid,)).fetchone()
            if old != (bucket,prio,must,delcand): changed += 1
            self.con.execute("""UPDATE mails SET bucket=?,priority=?,must_read=?,delete_candidate=?,reason=?,
                                work_score=?,personal_score=?,ad_score=? WHERE id=?""",
                             (bucket,prio,must,delcand,reason,ws,ps,ads,dbid))
        self.con.commit()
        return changed

    def learn_not_must(self): self._learn_selected('not_must')
    def learn_must(self): self._learn_selected('must')
    def learn_ad(self): self._learn_selected('ad')

    def manage_learning(self):
        win=tk.Toplevel(self); win.title('v2.6 학습 규칙 관리'); win.geometry('1000x520')
        cols=('id','rule','sender','pattern','created','source')
        tv=ttk.Treeview(win,columns=cols,show='headings',selectmode='extended')
        defs=[('id',55,'ID'),('rule',90,'학습'),('sender',220,'발신자'),('pattern',260,'제목 패턴'),
              ('created',140,'학습일시'),('source',330,'원본 제목')]
        for c,w,n in defs:
            tv.heading(c,text=n); tv.column(c,width=w,anchor='w')
        tv.pack(fill='both',expand=True,padx=8,pady=8)
        def reload_rules():
            tv.delete(*tv.get_children())
            rows=self.con.execute('SELECT id,rule,sender_key,pattern,created_at,source_subject FROM learning_rules ORDER BY id DESC').fetchall()
            for r in rows: tv.insert('', 'end', iid=str(r[0]), values=r)
        def delete_rules():
            ids=tv.selection()
            if not ids: return
            if not messagebox.askyesno('규칙 삭제',f'선택한 {len(ids)}개 학습 규칙을 삭제할까요?',parent=win): return
            self.con.executemany('DELETE FROM learning_rules WHERE id=?',[(x,) for x in ids])
            self.con.commit(); reload_rules()
        bar=ttk.Frame(win); bar.pack(fill='x',padx=8,pady=(0,8))
        ttk.Button(bar,text='선택 규칙 삭제',command=delete_rules).pack(side='left')
        ttk.Button(bar,text='닫기',command=win.destroy).pack(side='right')
        reload_rules()

    def classify_with_learning(self, subject, body, sender, has_attachment=False):
        key=parseaddr(sender or '')[1].lower()
        rr=self.con.execute('SELECT rule FROM sender_rules WHERE sender_key=?',(key,)).fetchone()
        srule=rr[0] if rr else ''
        base=score_mail(subject or '',body or '',sender or '',has_attachment,srule=='trusted',srule=='ad')
        lr=learned_rule(self.con,subject,sender)
        if not lr: return base
        text=((subject or '')+' '+(body or '')[:10000]+' '+(sender or '')).lower()
        hard=any(x.lower() in text for x in HARD_PROTECT)
        rule,_,_=lr
        if rule=='must':
            return '필수숙지','중요',1,0,'개인학습: 필수숙지 패턴',base[5],base[6],base[7]
        if hard:
            return base
        if rule=='ad':
            return '광고격리','일반',0,1,'개인학습: 광고 패턴 - 자동삭제 없음',base[5],base[6],base[7]
        if rule=='not_must':
            bucket='중요참고' if base[0]=='필수숙지' else base[0]
            return bucket,'일반',0,0,'개인학습: 필수숙지 제외 패턴',base[5],base[6],base[7]
        return base

    def set_filter(self,k): self.filter=k; self.q.set(''); self.refresh()

    def refresh(self):
        q=self.q.get().strip(); where=[]; args=[]
        if self.filter=='must': where.append("bucket='필수숙지' AND reviewed=0")
        elif self.filter=='important': where.append("bucket='중요참고'")
        elif self.filter=='normal': where.append("bucket='일반보관'")
        elif self.filter=='ad': where.append("bucket='광고격리' AND deleted_server=0")
        elif self.filter=='deleted': where.append('deleted_server=1')
        if q:
            where.append('(subject LIKE ? OR sender LIKE ? OR body LIKE ? OR summary LIKE ? OR reason LIKE ?)'); like=f'%{q}%'; args += [like]*5
        sql='SELECT id,sent_at,bucket,priority,reviewed,sender,subject,reason,attachments FROM mails'
        if where: sql += ' WHERE '+' AND '.join(where)
        sql += ' ORDER BY sent_at DESC LIMIT 2000'
        rows=self.con.execute(sql,args).fetchall(); self.tree.delete(*self.tree.get_children())
        for r in rows:
            review='완료' if r[4] else ('필수' if r[2]=='필수숙지' else '')
            self.tree.insert('', 'end', iid=str(r[0]), values=(r[1],r[2],r[3],review,r[5],r[6],r[7],'있음' if r[8] else ''))
        counts={
          'must':self.con.execute("SELECT count(*) FROM mails WHERE bucket='필수숙지' AND reviewed=0").fetchone()[0],
          'important':self.con.execute("SELECT count(*) FROM mails WHERE bucket='중요참고'").fetchone()[0],
          'normal':self.con.execute("SELECT count(*) FROM mails WHERE bucket='일반보관'").fetchone()[0],
          'ad':self.con.execute("SELECT count(*) FROM mails WHERE bucket='광고격리' AND deleted_server=0").fetchone()[0],
          'deleted':self.con.execute('SELECT count(*) FROM mails WHERE deleted_server=1').fetchone()[0]}
        labels={'must':'🔴 필수숙지','important':'🟠 중요참고','normal':'⚪ 일반보관','ad':'🗑 광고격리','deleted':'서버 삭제'}
        for k,b in self.cards.items(): b.config(text=f"{labels[k]} {counts[k]}건")
        self.status.set(f'{len(rows)}건 표시')


    def show_mail(self,event=None):
        sel=self.tree.selection()
        if not sel:return
        r=self.con.execute('SELECT sent_at,sender,subject,body,bucket,priority,reviewed,reason,summary,attachments FROM mails WHERE id=?',(sel[0],)).fetchone()
        win=tk.Toplevel(self); win.title(r[2] or '메일'); win.geometry('950x700')
        hdr=f"날짜: {r[0]}\n보낸 사람: {r[1]}\n구분: {r[4]} / 중요도: {r[5]} / 숙지: {'완료' if r[6] else '미완료'}\n판정 근거: {r[7]}\n제목: {r[2]}\n첨부: {r[9] or '-'}\n\n[핵심 요약]\n{r[8]}\n\n[원문]\n"
        txt=tk.Text(win,wrap='word'); txt.pack(fill='both',expand=True); txt.insert('1.0',hdr+r[3]); txt.config(state='disabled')
        if r[4]=='필수숙지' and not r[6]:
            self.con.execute('UPDATE mails SET reviewed=1 WHERE id=?',(sel[0],)); self.con.commit(); self.refresh()

    def sync_seen(self):
        user,pw=self.get_credentials()
        if not pw: return
        try:
            im=self.imap_login(user,pw)
            typ,_=im.select('INBOX',readonly=True)
            if typ!='OK': raise RuntimeError('받은메일함을 열 수 없습니다.')
            rows=self.con.execute("""SELECT id,uid FROM mails
                                     WHERE deleted_server=0 AND reviewed=0 AND uid IS NOT NULL AND uid<>''""").fetchall()
            seen_ids=[]; checked=0
            for start in range(0,len(rows),200):
                batch=rows[start:start+200]
                uidset=','.join(str(x[1]) for x in batch)
                typ,data=im.uid('fetch',uidset,'(FLAGS)')
                if typ!='OK': continue
                seen_uids=set()
                for item in data or []:
                    raw=item[0] if isinstance(item,tuple) else item
                    if not isinstance(raw,(bytes,bytearray)): continue
                    text=raw.decode('utf-8','ignore')
                    m=re.search(r'UID\s+(\d+)',text,re.I)
                    if m and re.search(r'\\Seen\b',text,re.I):
                        seen_uids.add(m.group(1))
                for dbid,uid in batch:
                    checked+=1
                    if str(uid) in seen_uids: seen_ids.append(dbid)
            if seen_ids:
                self.con.executemany('UPDATE mails SET reviewed=1 WHERE id=?',[(x,) for x in seen_ids])
                self.con.commit()
            im.logout(); self.refresh()
            remain=self.con.execute("""SELECT count(*) FROM mails
                                      WHERE bucket='필수숙지' AND reviewed=0 AND deleted_server=0""").fetchone()[0]
            messagebox.showinfo('읽음 상태 동기화',
                f'네이버 서버 읽음 상태를 확인했습니다.\n'
                f'확인 대상: {checked}건\n'
                f'이미 읽은 메일 → 숙지완료 반영: {len(seen_ids)}건\n'
                f'남은 미숙지 필수메일: {remain}건\n\n'
                '기존 숙지완료 상태는 되돌리지 않으며 서버 메일은 변경하지 않습니다.')
        except Exception as e:
            messagebox.showerror('읽음 동기화 오류',str(e))

    def focus_review(self):
        row=self.con.execute("""SELECT id,sent_at,sender,subject,summary,reason,attachments
                                FROM mails
                                WHERE bucket='필수숙지' AND reviewed=0 AND deleted_server=0
                                ORDER BY sent_at DESC LIMIT 1""").fetchone()
        if not row:
            messagebox.showinfo('집중 검토','미숙지 필수 메일이 없습니다.')
            return
        dbid,sent,sender,subject,summary,reason,attachments=row
        remain=self.con.execute("""SELECT count(*) FROM mails
                                  WHERE bucket='필수숙지' AND reviewed=0 AND deleted_server=0""").fetchone()[0]
        win=tk.Toplevel(self); win.title(f'필수숙지 집중 검토 - 남은 {remain}건'); win.geometry('920x560')
        info=(f'남은 미숙지: {remain}건\n날짜: {sent}\n보낸 사람: {sender}\n'
              f'제목: {subject}\n판정 근거: {reason}\n첨부: {attachments or "-"}\n\n'
              f'[핵심 요약]\n{summary or "(요약 없음)"}')
        txt=tk.Text(win,wrap='word',height=18); txt.pack(fill='both',expand=True,padx=10,pady=10)
        txt.insert('1.0',info); txt.config(state='disabled')
        bar=ttk.Frame(win); bar.pack(fill='x',padx=10,pady=(0,10))
        def select_main():
            self.set_filter('must')
            if self.tree.exists(str(dbid)):
                self.tree.selection_set(str(dbid)); self.tree.focus(str(dbid)); self.tree.see(str(dbid))
        def done():
            self.con.execute('UPDATE mails SET reviewed=1 WHERE id=?',(dbid,)); self.con.commit()
            win.destroy(); self.refresh(); self.focus_review()
        def learn(rule):
            select_main(); win.destroy(); self._learn_selected(rule)
        ttk.Button(bar,text='숙지 완료 → 다음',command=done).pack(side='left',padx=4)
        ttk.Button(bar,text='필수숙지 아님 학습',command=lambda:learn('not_must')).pack(side='left',padx=4)
        ttk.Button(bar,text='광고로 학습',command=lambda:learn('ad')).pack(side='left',padx=4)
        ttk.Button(bar,text='목록에서 보기',command=lambda:(select_main(),win.destroy())).pack(side='left',padx=4)
        ttk.Button(bar,text='닫기',command=win.destroy).pack(side='right',padx=4)

    def mark_reviewed(self):
        sel=self.tree.selection()
        if not sel:return
        self.con.executemany('UPDATE mails SET reviewed=1 WHERE id=?',[(x,) for x in sel]); self.con.commit(); self.refresh()

    def reclassify_1000(self):
        rows=self.con.execute("""SELECT id,subject,body,sender,attachments,reviewed
                                 FROM mails
                                 WHERE deleted_server=0
                                 ORDER BY sent_at DESC LIMIT 1000""").fetchall()
        if not rows:
            messagebox.showinfo('재분류','저장된 메일이 없습니다. 먼저 메일 수집·분석을 실행하세요.'); return
        if not messagebox.askyesno('전체 재분류',f'저장된 최근 {len(rows)}건을 v2.4 개인 맞춤 학습 엔진으로 다시 판정합니다.\n네이버 서버의 메일은 삭제하거나 이동하지 않습니다.\n계속할까요?'): return
        changed=0
        for i,(dbid,subj,body,sender,attachments,reviewed) in enumerate(rows,1):
            self.status.set(f'v2.4 재분류 중 {i}/{len(rows)}'); self.update_idletasks()
            bucket,prio,must,delcand,reason,ws,ps,ads=self.classify_with_learning(
                subj or '',body or '',sender or '',bool(attachments))
            old=self.con.execute('SELECT bucket,priority,must_read,delete_candidate FROM mails WHERE id=?',(dbid,)).fetchone()
            if old != (bucket,prio,must,delcand): changed += 1
            self.con.execute("""UPDATE mails
                                SET bucket=?,priority=?,must_read=?,delete_candidate=?,reason=?,
                                    work_score=?,personal_score=?,ad_score=?
                                WHERE id=?""",
                             (bucket,prio,must,delcand,reason,ws,ps,ads,dbid))
        self.con.commit(); self.filter='must'; self.refresh()
        counts={b:self.con.execute('SELECT count(*) FROM mails WHERE bucket=? AND deleted_server=0',(b,)).fetchone()[0]
                for b in ('필수숙지','중요참고','일반보관','광고격리')}
        messagebox.showinfo('재분류 완료',
            f'최근 {len(rows)}건 v2.4 재분류 완료\n'
            f'판정 변경: {changed}건\n\n'
            f'🔴 필수숙지: {counts["필수숙지"]}건\n'
            f'🟠 중요참고: {counts["중요참고"]}건\n'
            f'⚪ 일반보관: {counts["일반보관"]}건\n'
            f'🗑 광고격리: {counts["광고격리"]}건\n\n'
            '서버 삭제/이동은 수행하지 않았습니다.')

    def get_credentials(self):
        user=self.user.get().strip()
        if not user: messagebox.showwarning('입력 필요','네이버 ID 또는 메일주소를 입력하세요.'); return None,None
        if '@' not in user: user += '@naver.com'
        pw=simpledialog.askstring('애플리케이션 비밀번호','네이버 애플리케이션 비밀번호를 입력하세요.\n비밀번호는 저장하지 않습니다.',show='*',parent=self)
        return user,pw

    def imap_login(self,user,pw):
        im=imaplib.IMAP4_SSL(IMAP_HOST,IMAP_PORT,ssl_context=ssl.create_default_context()); im.login(user,pw); return im

    def delete_selected(self):
        sel=self.tree.selection()
        if not sel:return
        ids=[]
        for x in sel:
            row=self.con.execute('SELECT uid,delete_candidate,deleted_server,subject FROM mails WHERE id=?',(x,)).fetchone()
            if row and row[1] and not row[2]: ids.append((x,row[0],row[3]))
        if not ids:
            messagebox.showinfo('삭제 대상 없음','선택한 메일 중 고확신 광고 삭제후보가 없습니다.'); return
        if not messagebox.askyesno('영구삭제 확인',f'{len(ids)}건을 네이버 서버에서 영구삭제합니다.\n자동삭제가 아닌 수동 검토 삭제입니다. 네이버 서버에서 삭제됩니다. 계속할까요?'): return
        user,pw=self.get_credentials()
        if not pw:return
        try:
            im=self.imap_login(user,pw); im.select('INBOX',readonly=False)
            done=0
            for dbid,uid,_ in ids:
                typ,_=im.uid('STORE',uid,'+FLAGS.SILENT','(\\Deleted)')
                if typ=='OK': self.con.execute('UPDATE mails SET deleted_server=1 WHERE id=?',(dbid,)); done+=1
            im.expunge(); im.logout(); self.con.commit(); self.refresh(); messagebox.showinfo('완료',f'{done}건을 서버에서 삭제했습니다.')
        except Exception as e: messagebox.showerror('삭제 오류',str(e))

    def collect(self):
        user,pw=self.get_credentials()
        if not pw:return
        self.cfg['user']=user; save_config(self.cfg)
        self.status.set('서버 연결 중...'); self.update_idletasks()
        try:
            im=self.imap_login(user,pw); typ,_=im.select('INBOX',readonly=True)
            if typ!='OK': raise RuntimeError('받은메일함을 열 수 없습니다.')
            typ,data=im.uid('search',None,'ALL'); uids=data[0].split() if typ=='OK' else []
            existing={x[0] for x in self.con.execute('SELECT uid FROM mails').fetchall()}; new=deleted=0
            for i,ub in enumerate(uids,1):
                uid=ub.decode()
                if uid in existing: continue
                self.status.set(f'분석 중 {i}/{len(uids)} (신규 {new}, 광고삭제 {deleted})'); self.update_idletasks()
                typ,msgdata=im.uid('fetch',ub,'(RFC822)')
                if typ!='OK' or not msgdata or not isinstance(msgdata[0],tuple): continue
                msg=email.message_from_bytes(msgdata[0][1]); subj=decode_mime(msg.get('Subject')); sender=decode_mime(msg.get('From')); body=extract_text(msg)
                try: dt=parsedate_to_datetime(msg.get('Date')).astimezone().strftime('%Y-%m-%d %H:%M')
                except Exception: dt=''
                attach_parts=[]
                for p in msg.walk():
                    fn=decode_mime(p.get_filename())
                    if fn: attach_parts.append((p,fn))
                bucket,prio,must,delcand,reason,ws,ps,ads=self.classify_with_learning(subj,body,sender,bool(attach_parts))
                saved=[]
                # 중요메일 첨부만 자동 보존. 광고 첨부는 저장하지 않음.
                if bucket in ('필수숙지','중요참고'):
                    for p,fn in attach_parts:
                        payload=p.get_payload(decode=True)
                        if payload is None: continue
                        folder=ATTACH_DIR/(dt[:10] or 'unknown'); folder.mkdir(exist_ok=True)
                        path=folder/f'{uid}_{safe_name(fn)}'; path.write_bytes(payload); saved.append(str(path))
                deleted_server=0
                self.con.execute('''INSERT OR IGNORE INTO mails(uid,msgid,sent_at,sender,subject,body,bucket,priority,must_read,reviewed,delete_candidate,deleted_server,reason,summary,attachments,work_score,personal_score,ad_score)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(uid,msg.get('Message-ID',''),dt,sender,subj,body,bucket,prio,must,0,delcand,deleted_server,reason,summary_text(subj,body),' | '.join(saved),ws,ps,ads))
                self.con.commit(); new+=1
            im.logout(); self.refresh()
            messagebox.showinfo('완료',f'신규 메일 {new}건 분석 완료\n광고는 자동 삭제하지 않고 격리후보로만 표시합니다.\n\n필수숙지 메일만 미숙지 목록에 표시하고, 나머지는 중요참고/일반보관/광고격리로 분리합니다.')
        except imaplib.IMAP4.error as e:
            messagebox.showerror('로그인/IMAP 오류','네이버 IMAP 연결에 실패했습니다.\nIMAP/SMTP, 2단계 인증, 애플리케이션 비밀번호를 확인하세요.\n\n'+str(e))
        except Exception as e: messagebox.showerror('오류',str(e))
        finally: self.status.set('준비')

if __name__=='__main__': App().mainloop()
