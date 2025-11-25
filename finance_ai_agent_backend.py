import pandas as pd
import numpy as np
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Depends, status
from pydantic import BaseModel, Field
from typing import Dict, List, Any, Optional
import json
import uuid
from datetime import datetime
import hashlib  # 데이터 암호화 시뮬레이션용
import uvicorn
import io  # For file parsing simulation

# ==============================================================================
# 0. 전역 설정 및 DB/Storage/LLM 시뮬레이션
# ==============================================================================
app = FastAPI(
    title="Finance AI Agent Backend",
    description="재무제표 분석, 결측치 질의, 보고서 생성 및 교육 기능을 제공하는 AI Agent.",
    version="0.1.0"
)

# In-memory DB 및 Storage 시뮬레이션 (실제 프로덕션에서는 PostgreSQL, S3 사용)
IN_MEMORY_DB: Dict[str, Dict[str, Any]] = {
    "financial_data_header": {},
    "financial_data_line_items": {},
    "reconciliation_log": {},
    "glossary_terms": {}
}

S3_STORAGE: Dict[str, bytes] = {}  # 파일 원본 저장 시뮬레이션

# 용어 사전 (Glossary_Terms_T) 초기 데이터
IN_MEMORY_DB["glossary_terms"] = {
    "유동자산": {"쉬운 설명": "1년 안에 현금으로 바꿀 수 있는 자산.", "분석 기준": "단기 자금 동원력"},
    "유동부채": {"쉬운 설명": "1년 안에 갚아야 하는 빚.", "분석 기준": "단기 상환 의무"},
    "유동비율": {"쉬운 설명": "단기 빚을 갚을 능력을 보여주는 지표.", "분석 기준": "200% 이상이 양호"},
    "매출총이익률": {"쉬운 설명": "매출액에서 원가를 뺀 마진율.", "분석 기준": "핵심 사업 경쟁력"},
    "판관비": {"쉬운 설명": "물건을 팔거나 회사를 운영하는 데 들어가는 비용.", "분석 기준": "영업 효율성"},
    "단기 지급 능력": {"쉬운 설명": "가까운 시일(보통 1년 이내)에 예상되는 빚을 제때 갚을 수 있는 회사의 능력을 의미합니다.", "분석 기준": "유동비율과 당좌비율 등을 통해 측정합니다."},
    "매출원가": {"쉬운 설명": "물건을 만들거나 서비스를 제공하는 데 직접 들어간 비용입니다.", "분석 기준": "주력 사업의 비용 효율성"}
}

CRITICAL_BS_ITEMS = ['현금', '매출채권', '재고자산', '단기차입금', '자본금']  # 결측치 발생 시 필수 질문 항목
CRITICAL_IS_ITEMS = ['매출액', '매출원가', '영업이익']
CRITICAL_CFS_ITEMS = ['영업활동현금흐름', '투자활동현금흐름', '재무활동현금흐름']


# ==============================================================================
# 1. Pydantic 모델: 데이터 유효성 검사 및 응답 구조 정의
# ==============================================================================

class User(BaseModel):
    id: str = Field(..., description="사용자 고유 ID")
    role: str = Field(..., description="사용자 역할 (Admin, Manager, Analyst, Auditor)")


# 인증/권한 관리 시뮬레이션
# 실제로는 DB에서 사용자 정보 조회 및 JWT 토큰 검증
def get_current_user(user_id: str = "test_user_id", role: str = "Analyst") -> User:
    # 이 함수는 실제 JWT 토큰에서 사용자 ID와 역할을 추출하는 로직으로 대체됩니다.
    return User(id=user_id, role=role)


class FinancialStatementLine(BaseModel):
    statement_type: str = Field(..., description="재무제표 종류 (BS, IS, CFS)")
    standard_account: str = Field(..., description="표준 계정명")
    amount: Optional[float] = Field(None, description="금액 (누락 시 None)")
    as_of_date: datetime = Field(..., description="기준 날짜")


class ProjectHeader(BaseModel):
    project_id: str
    user_id: str
    upload_date: datetime
    fiscal_year: int
    status: str


class ReconciliationRequest(BaseModel):
    project_id: str
    statement_type: str
    item_requested: str
    user_provided_value: float


class ReportResponse(BaseModel):
    project_id: str
    status: str
    executive_summary: str
    detailed_analysis: Dict[str, Any]
    glossary: Dict[str, Any]
    reconciliation_log: List[Dict[str, Any]]


# ==============================================================================
# 2. Agent Core Logic (FinanceAIAgent 클래스)
#    - 앞서 구현한 로직을 API에 통합하기 위한 형태로 변환
# ==============================================================================

class FinanceAIAgentCore:
    def __init__(self, project_id: str, user: User):
        self.project_id = project_id
        self.user = user
        self.bs_df: Optional[pd.DataFrame] = None
        self.is_df: Optional[pd.DataFrame] = None
        self.cfs_df: Optional[pd.DataFrame] = None
        self.reconciliation_log: List[Dict[str, Any]] = []
        self.analysis_results: Dict[str, Any] = {}

        # DB에서 데이터 로드 (시뮬레이션)
        self._load_data_from_db()

    def _load_data_from_db(self):
        """DB에서 프로젝트 데이터 로드 시뮬레이션"""
        line_items = IN_MEMORY_DB["financial_data_line_items"].get(self.project_id, [])
        if not line_items:
            # print(f"DEBUG: No line items found for project {self.project_id}")
            return  # 데이터가 없으면 초기화하지 않음

        df_list = []
        for item_data in line_items:
            df_list.append(pd.DataFrame([{
                '항목': item_data['standard_account'],
                item_data['as_of_date'].year: item_data['amount']
            }]).set_index('항목'))

        if df_list:
            # 연도별로 데이터를 통합 (pivot table처럼)
            combined_df = pd.concat(df_list, axis=1, sort=True)
            # 결측치를 위해 연도별 컬럼을 통합
            self.bs_df = combined_df.filter(regex='(?!^$)').dropna(axis=1, how='all')  # 빈 컬럼 제거
            self.is_df = combined_df.filter(regex='(?!^$)').dropna(axis=1, how='all')
            self.cfs_df = combined_df.filter(regex='(?!^$)').dropna(axis=1, how='all')

            # 결측치 처리 (NaN을 명시적으로 None으로 바꿔야 Pydantic이 인식)
            if self.bs_df is not None: self.bs_df = self.bs_df.applymap(lambda x: None if pd.isna(x) else x)
            if self.is_df is not None: self.is_df = self.is_df.applymap(lambda x: None if pd.isna(x) else x)
            if self.cfs_df is not None: self.cfs_df = self.cfs_df.applymap(lambda x: None if pd.isna(x) else x)

        # 로그 로드
        self.reconciliation_log = IN_MEMORY_DB["reconciliation_log"].get(self.project_id, [])

    def _standardize_data(self, file_content: pd.DataFrame, statement_type: str) -> pd.DataFrame:
        """(전략 1) 데이터 표준화 및 인덱스 설정 (실제 LLM 매핑 로직 필요)"""
        # 여기서는 간단히 '항목' 컬럼을 인덱스로 설정하는 것으로 시뮬레이션
        # LLM을 통해 다양한 계정명을 표준 계정명으로 매핑하는 로직이 여기에 들어갑니다.
        # 예: '현금및현금성자산' -> '현금', '매출원가' -> 'COGS'
        # 지금은 입력된 '항목' 컬럼 값을 그대로 표준 계정명으로 사용한다고 가정.
        return file_content.set_index('항목').copy()

    def _detect_missing_critical_items(self) -> Dict[str, List[str]]:
        """(Core Loop 1) 필수 항목 결측치 감지"""
        missing: Dict[str, List[str]] = {}

        # BS 결측치 감지
        if self.bs_df is not None and not self.bs_df.empty:
            latest_bs_col = self.bs_df.columns[-1]
            missing_bs = [item for item in CRITICAL_BS_ITEMS if
                          item in self.bs_df.index and pd.isna(self.bs_df.loc[item, latest_bs_col])]
            if missing_bs: missing['BS'] = missing_bs

        # IS 결측치 감지
        if self.is_df is not None and not self.is_df.empty:
            latest_is_col = self.is_df.columns[-1]
            missing_is = [item for item in CRITICAL_IS_ITEMS if
                          item in self.is_df.index and pd.isna(self.is_df.loc[item, latest_is_col])]
            if missing_is: missing['IS'] = missing_is

        # CFS 결측치 감지 (현재 CFS 데이터는 없으므로 시뮬레이션 안 함)

        return missing

    def reconcile_data(self, statement_type: str, item: str, value: float):
        """(Core Loop 2) 결측치 질의 및 사용자 입력 처리"""
        latest_col = None
        if statement_type == 'BS' and self.bs_df is not None:
            if item in self.bs_df.index:
                latest_col = self.bs_df.columns[-1]
                self.bs_df.loc[item, latest_col] = value

        elif statement_type == 'IS' and self.is_df is not None:
            if item in self.is_df.index:
                latest_col = self.is_df.columns[-1]
                self.is_df.loc[item, latest_col] = value

        else:
            raise HTTPException(status_code=400, detail=f"'{statement_type}' 재무제표에 '{item}' 항목이 없거나 처리할 수 없습니다.")

        if latest_col:
            self.reconciliation_log.append({
                'timestamp': datetime.now().isoformat(),
                'statement_type': statement_type,
                'item': item,
                'provided_value': value,
                'agent_action': 'Filled by User Input',
                'fiscal_year': latest_col  # 어떤 연도 데이터인지 기록
            })
            # DB 로그 업데이트 (실제 DB에서는 여기에 UPDATE 쿼리)
            IN_MEMORY_DB["reconciliation_log"].setdefault(self.project_id, []).append(self.reconciliation_log[-1])

    def _analyze_financials(self):
        """(Core Loop 3) BS 및 IS 핵심 재무 비율 분석"""
        latest_bs_col = self.bs_df.columns[-1] if self.bs_df is not None and not self.bs_df.empty else None
        latest_is_col = self.is_df.columns[-1] if self.is_df is not None and not self.is_df.empty else None

        if latest_bs_col and '현금' in self.bs_df.index and '매출채권' in self.bs_df.index and '재고자산' in self.bs_df.index and '단기차입금' in self.bs_df.index:
            # 1. BS 분석: 유동비율 (Current Ratio)
            current_assets = (
                    self.bs_df.loc['현금', latest_bs_col] +
                    self.bs_df.loc['매출채권', latest_bs_col] +
                    (self.bs_df.loc['재고자산', latest_bs_col] if '재고자산' in self.bs_df.index else 0)
            )
            current_liabilities = self.bs_df.loc['단기차입금', latest_bs_col]
            self.analysis_results['유동자산'] = current_assets
            self.analysis_results['유동부채'] = current_liabilities
            self.analysis_results['유동비율'] = current_assets / current_liabilities if current_liabilities else 0
        else:
            self.analysis_results['유동비율'] = None  # 데이터 부족으로 분석 불가

        if latest_is_col and '매출액' in self.is_df.index and '매출원가' in self.is_df.index and '판관비' in self.is_df.index:
            # 2. IS 분석: 매출총이익률 (GP Margin) 및 영업이익률 (OP Margin)
            revenue = self.is_df.loc['매출액', latest_is_col]
            cogs = self.is_df.loc['매출원가', latest_is_col]
            sgna = self.is_df.loc['판관비', latest_is_col]

            gross_profit = revenue - cogs
            operating_income = gross_profit - sgna

            self.analysis_results['매출총이익률'] = gross_profit / revenue if revenue else 0
            self.analysis_results['영업이익률'] = operating_income / revenue if revenue else 0
        else:
            self.analysis_results['매출총이익률'] = None
            self.analysis_results['영업이익률'] = None

        # 3. CFS 분석 (시뮬레이션: 데이터가 없으므로 코멘트만)
        self.analysis_results['이익의질'] = "데이터 부족"  # 실제로는 CFS 분석 로직 추가

    def _generate_report_content(self) -> Dict[str, Any]:
        """(Core Loop 4) 보고서 구조화 및 UX 태그 생성"""
        # LLM을 호출하여 분석 코멘트 생성 (여기는 시뮬레이션)
        # LLM에게 '유동비율', '매출총이익률' 등의 용어를 태그해달라고 요청

        ratio = self.analysis_results.get('유동비율')
        gp_margin = self.analysis_results.get('매출총이익률')
        op_margin = self.analysis_results.get('영업이익률')

        executive_summary = "📈 **AI Agent의 핵심 재무 진단**\n"
        detailed_analysis = {}

        # A. Executive Summary (첫 장 요약 전략)
        if ratio is not None:
            executive_summary += f"   - 회사의 [[유동비율]]은 **{ratio:.2f}배**로, [[단기 지급 능력]]은 매우 안정적입니다. (2.0배 기준 양호)\n"
        if gp_margin is not None:
            executive_summary += f"   - [[매출총이익률]]은 **{gp_margin:.2%}**입니다. 이는 주력 사업의 마진 경쟁력이 우수함을 나타냅니다.\n"
        if op_margin is not None:
            executive_summary += f"   - [[영업이익률]]은 **{op_margin:.2%}**입니다. 영업 효율성도 양호합니다.\n"

        executive_summary += "\n**최종 결론:** 전반적으로 양호한 재무 상태를 유지하고 있으나, 특정 비용 항목에 대한 추가 분석이 필요합니다."

        # B. What-Why-Action (IS 상세 분석 시뮬레이션)
        detailed_analysis['수익성 분석'] = {
            "title": "수익성 및 효율성 분석",
            "what": f"[[매출총이익률]]이 {gp_margin:.2%}로 높게 유지되고 있습니다.",
            "why": "이는 [[매출원가]] 통제에 성공했거나 고마진 제품의 판매 비중이 높기 때문으로 추정됩니다.",
            "action": "고마진 제품 판매 채널을 확장하고, 경쟁사 대비 [[매출원가]] 효율성을 검토할 것을 권고합니다."
        }

        return {
            "executive_summary": executive_summary,
            "detailed_analysis": detailed_analysis,
            "glossary": IN_MEMORY_DB["glossary_terms"],  # 전체 용어 사전
            "reconciliation_log": self.reconciliation_log
        }

    def run_full_analysis(self) -> Dict[str, Any]:
        """Agent의 메인 실행 흐름"""
        missing_items = self._detect_missing_critical_items()

        if missing_items:
            # 결측치 발생 시, FE에 어떤 데이터가 필요한지 반환하여 사용자 입력을 유도
            return {
                "status": "AWAITING_RECONCILIATION",
                "missing_items": missing_items,
                "message": "필수 재무 데이터가 누락되었습니다. 입력해주세요."
            }

        self._analyze_financials()
        report_content = self._generate_report_content()

        return {
            "status": "COMPLETED",
            **report_content
        }


# ==============================================================================
# 3. FastAPI 엔드포인트: FE와 BE 연결 (API 설계)
# ==============================================================================

@app.post("/upload-financial-data", summary="재무제표 파일 업로드 및 초기 처리")
async def upload_financial_data(
        file: UploadFile = File(...),
        fiscal_year: int = Form(..., description="회계연도"),
        statement_type: str = Form(..., description="재무제표 종류 (BS, IS, CFS)"),
        current_user: User = Depends(get_current_user)
):
    """
    사용자가 재무제표 파일을 업로드하고, 백엔드에서 이를 표준화하여 DB에 저장합니다.
    """
    project_id = str(uuid.uuid4())
    upload_date = datetime.now()

    error_template = """파일 업로드에 실패했습니다. 다음 주의사항을 확인해주세요:

1.  **파일 형식**: `CSV` 또는 `Excel` 파일만 지원됩니다.
2.  **필수 컬럼**:
    - **'항목' 컬럼**: 재무 계정 이름(예: '현금', '매출액')이 포함된 '항목' 컬럼이 반드시 있어야 합니다.
    - **'연도' 컬럼**: API에 입력한 회계연도(예: {fiscal_year})와 동일한 이름의 컬럼이 파일에 있어야 합니다.

**오류 원인**: {specific_error}
"""

    try:
        file_content_bytes = await file.read()

        # S3_STORAGE에 파일 원본 저장 시뮬레이션 (해시값으로 파일명 대체)
        file_hash = hashlib.sha256(file_content_bytes).hexdigest()
        S3_STORAGE[file_hash] = file_content_bytes

        # 파일 파싱 (여기서는 Excel/CSV만 시뮬레이션)
        if file.filename.endswith('.csv'):
            df = pd.read_csv(io.StringIO(file_content_bytes.decode('utf-8')))
        elif file.filename.endswith(('.xls', '.xlsx')):
            df = pd.read_excel(io.BytesIO(file_content_bytes))
        else:
            raise ValueError("지원하지 않는 파일 형식입니다. CSV 또는 Excel 파일을 업로드해주세요.")

        # 컬럼 이름의 데이터 타입을 문자열로 통일하여 예측 가능성 확보
        df.columns = [str(c) for c in df.columns]
        fiscal_year_str = str(fiscal_year)

        # 파일 유효성 검사
        if '항목' not in df.columns:
            raise ValueError("파일에 '항목' 컬럼이 없습니다.")
        if fiscal_year_str not in df.columns:
            raise ValueError(f"파일에 '{fiscal_year_str}'년 컬럼이 없습니다.")

        agent_core = FinanceAIAgentCore(project_id=project_id, user=current_user)
        standardized_df = agent_core._standardize_data(df, statement_type)

        # DB에 저장 (Financial_Data_Line_Items_T)
        IN_MEMORY_DB["financial_data_line_items"][project_id] = []
        for index, row in standardized_df.iterrows():
            item_data = FinancialStatementLine(
                statement_type=statement_type,
                standard_account=index,
                amount=row[fiscal_year_str] if fiscal_year_str in row.index and not pd.isna(row[fiscal_year_str]) else None,
                as_of_date=datetime(fiscal_year, 12, 31)  # 연말 기준으로 가정
            ).dict()
            IN_MEMORY_DB["financial_data_line_items"][project_id].append(item_data)

        # DB에 프로젝트 헤더 저장 (Financial_Data_Header_T)
        header = ProjectHeader(
            project_id=project_id,
            user_id=current_user.id,
            upload_date=upload_date,
            fiscal_year=fiscal_year,
            status="Uploaded"
        )
        IN_MEMORY_DB["financial_data_header"][project_id] = header.dict()

        return {"project_id": project_id, "status": "Uploaded", "message": "파일 업로드 및 표준화 완료. 분석을 시작해주세요."}

    except ValueError as e:
        specific_error = str(e)
        raise HTTPException(status_code=400, detail=error_template.format(specific_error=specific_error, fiscal_year=fiscal_year))
    except Exception as e:
        # 예상치 못한 오류에 대한 처리
        specific_error = f"서버 내부 오류가 발생했습니다: {e}"
        raise HTTPException(status_code=500, detail=error_template.format(specific_error=specific_error, fiscal_year=fiscal_year))


@app.post("/analyze/{project_id}", summary="재무 분석 실행 및 보고서 생성")
async def analyze_financials(
        project_id: str,
        current_user: User = Depends(get_current_user)
):
    """
    특정 프로젝트 ID에 대해 재무 분석을 실행하고 보고서를 생성합니다.
    결측치가 있으면 사용자 입력을 요청하는 응답을 반환합니다.
    """
    # 권한 검사 (현재 사용자가 해당 project_id에 접근 권한이 있는지 확인)
    project_header = IN_MEMORY_DB["financial_data_header"].get(project_id)
    if not project_header or project_header['user_id'] != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="접근 권한이 없습니다.")

    agent_core = FinanceAIAgentCore(project_id=project_id, user=current_user)

    # 여기서 Agent의 전체 분석 흐름이 실행됩니다.
    analysis_result = agent_core.run_full_analysis()

    if analysis_result["status"] == "AWAITING_RECONCILIATION":
        # 결측치 발견 시, FE에 어떤 데이터를 요청해야 하는지 알려줌
        return analysis_result

    # 분석 완료 후 DB에 상태 업데이트
    IN_MEMORY_DB["financial_data_header"][project_id]["status"] = "Completed"

    return ReportResponse(
        project_id=project_id,
        status="Completed",
        executive_summary=analysis_result["executive_summary"],
        detailed_analysis=analysis_result["detailed_analysis"],
        glossary=analysis_result["glossary"],
        reconciliation_log=analysis_result["reconciliation_log"]
    )


@app.put("/reconcile-missing-data/{project_id}", summary="누락된 재무 데이터 사용자 입력")
async def reconcile_missing_data(
        project_id: str,
        request: ReconciliationRequest,
        current_user: User = Depends(get_current_user)
):
    """
    Agent가 요청한 누락된 재무 데이터를 사용자가 입력하여 업데이트합니다.
    """
    # 권한 검사
    project_header = IN_MEMORY_DB["financial_data_header"].get(project_id)
    if not project_header or project_header['user_id'] != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="접근 권한이 없습니다.")

    agent_core = FinanceAIAgentCore(project_id=project_id, user=current_user)
    try:
        agent_core.reconcile_data(
            statement_type=request.statement_type,
            item=request.item_requested,
            value=request.user_provided_value
        )
        return {"project_id": project_id, "status": "Reconciled",
                "message": f"'{request.item_requested}' 데이터가 업데이트되었습니다."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/report/{project_id}", summary="생성된 재무 보고서 조회")
async def get_report(
        project_id: str,
        current_user: User = Depends(get_current_user)
):
    """
    이미 생성된 재무 보고서를 조회합니다.
    """
    # 권한 검사
    project_header = IN_MEMORY_DB["financial_data_header"].get(project_id)
    if not project_header or project_header['user_id'] != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="접근 권한이 없습니다.")

    agent_core = FinanceAIAgentCore(project_id=project_id, user=current_user)
    analysis_result = agent_core.run_full_analysis()  # 다시 분석을 돌려 최신 상태의 보고서 생성

    if analysis_result["status"] == "AWAITING_RECONCILIATION":
        raise HTTPException(status_code=400, detail="보고서를 보기 전에 누락된 데이터를 먼저 입력해야 합니다.")

    return ReportResponse(
        project_id=project_id,
        status="Completed",
        executive_summary=analysis_result["executive_summary"],
        detailed_analysis=analysis_result["detailed_analysis"],
        glossary=analysis_result["glossary"],
        reconciliation_log=analysis_result["reconciliation_log"]
    )

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)