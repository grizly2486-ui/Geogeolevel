"""
Geogeolevel v5.0 - Полный редизайн + поддержка BBFF/BFFB
"""

import io
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Tuple, Set

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


# ============================================================
# МОДЕЛИ ДАННЫХ
# ============================================================

@dataclass
class RawReading:
    """Один отсчет по рейке"""
    point: str
    reading: float
    distance: float
    reading_type: str  # 'Rb' или 'Rf'


@dataclass
class RawMeasurement:
    """Превышение между двумя точками"""
    back_point: str
    fore_point: str
    back_reading: float
    fore_reading: float
    back_dist: float
    fore_dist: float


@dataclass
class Station:
    """Станция с усредненным превышением"""
    station_id: int
    back_point: str
    fore_point: str
    dh_mean: float
    distance: float
    measurements: List[RawMeasurement] = field(default_factory=list)


@dataclass
class LevelingRun:
    """Нивелирный ход"""
    run_id: int
    stations: List[Station]
    start_point: str
    end_point: str


@dataclass
class AdjustedPoint:
    """Уравненная точка"""
    point_id: str
    h_original: Optional[float]
    h_adjusted: float
    correction: float
    distance_from_start: float


# ============================================================
# ПАРСЕР TRIMBLE DINI (BBFF и BFFB)
# ============================================================

class TrimbleDiniParser:
    """Парсер для Trimble DiNi с автоопределением схемы BBFF/BFFB"""
    
    def __init__(self, raw_bytes: bytes):
        self.raw_bytes = raw_bytes

    def parse(self) -> Tuple[str, List[RawMeasurement], Dict[str, float], str]:
        """
        Возвращает:
        - job_name: имя работы
        - measurements: список измерений
        - points_with_heights: точки с отметками
        - scheme: обнаруженная схема ('BBFF' или 'BFFB')
        """
        text = self._decode_text()
        lines = text.splitlines()
        
        readings = []
        points_with_heights = {}
        job_name = "Unknown Job"
        
        for line in lines[:5]:
            if 'TO' in line and '.dat' in line.lower():
                match = re.search(r'TO\s+(\S+\.dat)', line, re.IGNORECASE)
                if match:
                    job_name = match.group(1).replace('.dat', '')
                break
        
        # Собираем все отсчеты
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            z_match = re.search(r'\|Z\s+([+-]?\d+\.?\d*)\s*m\s*\|', line)
            if z_match:
                point_match = re.search(r'\|KD1\s+(\S+)\s+', line)
                if point_match:
                    points_with_heights[point_match.group(1)] = float(z_match.group(1))
                continue
            
            rb_match = re.search(r'\|Rb\s+([+-]?\d+\.?\d*)\s*m\s*\|HD\s+([+-]?\d+\.?\d*)\s*m\s*\|', line)
            if rb_match:
                point_match = re.search(r'\|KD1\s+(\S+)\s+', line)
                if point_match:
                    readings.append(RawReading(
                        point=point_match.group(1),
                        reading=float(rb_match.group(1)),
                        distance=float(rb_match.group(2)),
                        reading_type='Rb'
                    ))
                continue
            
            rf_match = re.search(r'\|Rf\s+([+-]?\d+\.?\d*)\s*m\s*\|HD\s+([+-]?\d+\.?\d*)\s*m\s*\|', line)
            if rf_match:
                point_match = re.search(r'\|KD1\s+(\S+)\s+', line)
                if point_match:
                    readings.append(RawReading(
                        point=point_match.group(1),
                        reading=float(rf_match.group(1)),
                        distance=float(rf_match.group(2)),
                        reading_type='Rf'
                    ))
        
        # Определяем схему и группируем
        scheme = self._detect_scheme(readings)
        measurements = self._group_readings(readings, scheme)
        
        return job_name, measurements, points_with_heights, scheme

    def _detect_scheme(self, readings: List[RawReading]) -> str:
        """Определяет схему по первым отсчетам"""
        # Ищем первую пару отсчетов
        for i in range(len(readings) - 1):
            if readings[i].reading_type == 'Rb':
                # Если после Rb идет Rb — это BBFF
                if readings[i + 1].reading_type == 'Rb':
                    return 'BBFF'
                # Если после Rb идет Rf — это BFFB
                elif readings[i + 1].reading_type == 'Rf':
                    return 'BFFB'
        return 'BFFB'  # По умолчанию

    def _group_readings(self, readings: List[RawReading], scheme: str) -> List[RawMeasurement]:
        """Группирует отсчеты по станциям согласно схеме"""
        measurements = []
        i = 0
        
        while i < len(readings):
            if scheme == 'BBFF':
                # BBFF: Rb, Rb, Rf, Rf
                if i + 3 < len(readings):
                    if (readings[i].reading_type == 'Rb' and 
                        readings[i+1].reading_type == 'Rb' and
                        readings[i+2].reading_type == 'Rf' and 
                        readings[i+3].reading_type == 'Rf'):
                        
                        # Первое измерение
                        measurements.append(RawMeasurement(
                            back_point=readings[i].point,
                            fore_point=readings[i+2].point,
                            back_reading=readings[i].reading,
                            fore_reading=readings[i+2].reading,
                            back_dist=readings[i].distance,
                            fore_dist=readings[i+2].distance
                        ))
                        
                        # Второе измерение (контрольное)
                        measurements.append(RawMeasurement(
                            back_point=readings[i+1].point,
                            fore_point=readings[i+3].point,
                            back_reading=readings[i+1].reading,
                            fore_reading=readings[i+3].reading,
                            back_dist=readings[i+1].distance,
                            fore_dist=readings[i+3].distance
                        ))
                        
                        i += 4
                        continue
            else:
                # BFFB: Rb, Rf, Rf, Rb
                if i + 3 < len(readings):
                    if (readings[i].reading_type == 'Rb' and 
                        readings[i+1].reading_type == 'Rf' and
                        readings[i+2].reading_type == 'Rf' and 
                        readings[i+3].reading_type == 'Rb'):
                        
                        # Первое измерение
                        measurements.append(RawMeasurement(
                            back_point=readings[i].point,
                            fore_point=readings[i+1].point,
                            back_reading=readings[i].reading,
                            fore_reading=readings[i+1].reading,
                            back_dist=readings[i].distance,
                            fore_dist=readings[i+1].distance
                        ))
                        
                        # Второе измерение (контрольное)
                        measurements.append(RawMeasurement(
                            back_point=readings[i+3].point,
                            fore_point=readings[i+2].point,
                            back_reading=readings[i+3].reading,
                            fore_reading=readings[i+2].reading,
                            back_dist=readings[i+3].distance,
                            fore_dist=readings[i+2].distance
                        ))
                        
                        i += 4
                        continue
            
            i += 1
        
        return measurements

    def _decode_text(self) -> str:
        for enc in ['utf-8', 'windows-1251', 'cp1251', 'latin-1']:
            try:
                return self.raw_bytes.decode(enc)
            except UnicodeDecodeError:
                continue
        return self.raw_bytes.decode('latin-1', errors='ignore')


# ============================================================
# АНАЛИЗАТОР СТРУКТУРЫ ХОДОВ
# ============================================================

class RunAnalyzer:
    def __init__(self, measurements: List[RawMeasurement], points_with_heights: Dict[str, float]):
        self.measurements = measurements
        self.points_with_heights = points_with_heights
    
    def group_into_stations(self) -> List[Station]:
        station_groups = defaultdict(list)
        
        for m in self.measurements:
            key = (m.back_point, m.fore_point)
            station_groups[key].append(m)
        
        stations = []
        for idx, ((back_pt, fore_pt), meas_list) in enumerate(station_groups.items(), 1):
            dh_values = [m.back_reading - m.fore_reading for m in meas_list]
            dist_values = [m.back_dist + m.fore_dist for m in meas_list]
            
            dh_mean = sum(dh_values) / len(dh_values)
            dist_mean = sum(dist_values) / len(dist_values)
            
            stations.append(Station(
                station_id=idx,
                back_point=back_pt,
                fore_point=fore_pt,
                dh_mean=dh_mean,
                distance=dist_mean,
                measurements=meas_list
            ))
        
        return stations
    
    def identify_runs(self, stations: List[Station]) -> List[LevelingRun]:
        if not stations:
            return []
        
        visited = set()
        runs = []
        run_id = 1
        
        for station in stations:
            if station.back_point not in visited:
                component_stations = []
                queue = [station.back_point]
                component_points = set()
                
                while queue:
                    point = queue.pop(0)
                    if point in visited:
                        continue
                    visited.add(point)
                    component_points.add(point)
                    
                    for s in stations:
                        if s.back_point == point or s.fore_point == point:
                            if s not in component_stations:
                                component_stations.append(s)
                                other_point = s.fore_point if s.back_point == point else s.back_point
                                if other_point not in visited:
                                    queue.append(other_point)
                
                ordered_stations = self._order_stations(component_stations, component_points)
                
                if ordered_stations:
                    runs.append(LevelingRun(
                        run_id=run_id,
                        stations=ordered_stations,
                        start_point=ordered_stations[0].back_point,
                        end_point=ordered_stations[-1].fore_point
                    ))
                    run_id += 1
        
        return runs
    
    def _order_stations(self, stations: List[Station], points: Set[str]) -> List[Station]:
        if not stations:
            return []
        
        start_candidates = [s.back_point for s in stations if s.back_point in self.points_with_heights]
        start_point = start_candidates[0] if start_candidates else stations[0].back_point
        
        ordered = []
        current_point = start_point
        used_stations = set()
        
        while True:
            next_station = None
            for s in stations:
                if s.back_point == current_point and id(s) not in used_stations:
                    next_station = s
                    break
            
            if next_station is None:
                break
            
            ordered.append(next_station)
            used_stations.add(id(next_station))
            current_point = next_station.fore_point
        
        return ordered


# ============================================================
# УРАВНИВАТЕЛЬ
# ============================================================

class LevelingAdjustment:
    def __init__(self, run: LevelingRun, h_start: float, h_end: Optional[float],
                 level_class: str = "technical", misclosure_mode: str = "distance"):
        self.run = run
        self.h_start = h_start
        self.h_end = h_end if h_end is not None else h_start
        self.level_class = level_class
        self.misclosure_mode = misclosure_mode

    def calculate(self) -> dict:
        if not self.run.stations:
            return {"error": "Нет станций в ходе"}

        sum_dh = sum(s.dh_mean for s in self.run.stations)
        sum_dist = sum(s.distance for s in self.run.stations)
        n_stations = len(self.run.stations)

        if sum_dist == 0:
            return {"error": "Сумма расстояний равна нулю"}

        dh_theoretical = self.h_end - self.h_start
        misclosure_m = sum_dh - dh_theoretical
        misclosure_mm = misclosure_m * 1000

        if self.misclosure_mode == "stations":
            allowed_mm = self._get_allowed_by_stations(n_stations)
            length_km = sum_dist / 1000.0
        else:
            length_km = sum_dist / 1000.0
            allowed_mm = self._get_allowed_by_distance(length_km)

        is_valid = abs(misclosure_mm) <= allowed_mm

        return {
            "run_id": self.run.run_id,
            "start_point": self.run.start_point,
            "end_point": self.run.end_point,
            "sum_dh_m": round(sum_dh, 4),
            "dh_theoretical_m": round(dh_theoretical, 4),
            "misclosure_mm": round(misclosure_mm, 2),
            "allowed_mm": round(allowed_mm, 2),
            "length_km": round(length_km, 3),
            "n_stations": n_stations,
            "is_valid": is_valid,
            "points": self._distribute_corrections(misclosure_m, sum_dist) if is_valid else [],
            "level_class": self.level_class,
            "formula_used": self._get_formula_text(length_km, n_stations),
            "error": f"Невязка {misclosure_mm:.2f} мм превышает допуск {allowed_mm:.2f} мм!" if not is_valid else None
        }

    def _get_allowed_by_distance(self, length_km: float) -> float:
        formulas = {
            "I": 2.0 * math.sqrt(length_km),
            "II": 5.0 * math.sqrt(length_km),
            "III": 10.0 * math.sqrt(length_km),
            "IV": 20.0 * math.sqrt(length_km),
            "technical": 50.0 * math.sqrt(length_km),
        }
        return formulas.get(self.level_class, 50.0 * math.sqrt(length_km))

    def _get_allowed_by_stations(self, n_stations: int) -> float:
        formulas = {
            "I": 0.5 * math.sqrt(n_stations),
            "II": 0.5 * math.sqrt(n_stations),
            "III": 1.0 * math.sqrt(n_stations),
            "IV": 2.0 * math.sqrt(n_stations),
            "technical": 5.0 * math.sqrt(n_stations),
        }
        return formulas.get(self.level_class, 5.0 * math.sqrt(n_stations))

    def _get_formula_text(self, length_km: float, n_stations: int) -> str:
        if self.misclosure_mode == "stations":
            coeffs = {"I": "0.5", "II": "0.5", "III": "1.0", "IV": "2.0", "technical": "5.0"}
            coeff = coeffs.get(self.level_class, "5.0")
            return f"f_доп = ±{coeff} × √{n_stations} = ±{self._get_allowed_by_stations(n_stations):.2f} мм"
        else:
            coeffs = {"I": "2.0", "II": "5.0", "III": "10.0", "IV": "20.0", "technical": "50.0"}
            coeff = coeffs.get(self.level_class, "50.0")
            return f"f_доп = ±{coeff} × √{length_km:.3f} = ±{self._get_allowed_by_distance(length_km):.2f} мм"

    def _distribute_corrections(self, misclosure_m: float, sum_dist: float) -> List[AdjustedPoint]:
        points = []
        current_h = self.h_start
        cumulative_dist = 0.0

        points.append(AdjustedPoint(
            point_id=self.run.stations[0].back_point,
            h_original=self.h_start,
            h_adjusted=self.h_start,
            correction=0.0,
            distance_from_start=0.0
        ))

        for station in self.run.stations:
            correction_m = -misclosure_m * (station.distance / sum_dist)
            dh_adjusted = station.dh_mean + correction_m
            current_h += dh_adjusted
            cumulative_dist += station.distance

            points.append(AdjustedPoint(
                point_id=station.fore_point,
                h_original=None,
                h_adjusted=round(current_h, 4),
                correction=round(correction_m, 4),
                distance_from_start=cumulative_dist
            ))

        if len(points) > 1:
            points[-1].h_adjusted = self.h_end
            last_dh_adj = self.h_end - points[-2].h_adjusted
            points[-1].correction = last_dh_adj - self.run.stations[-1].dh_mean

        return points


# ============================================================
# ИНТЕРФЕЙС
# ============================================================

st.set_page_config(page_title="Geogeolevel", page_icon="", layout="wide")

# ===== СТИЛИ =====
st.markdown("""
<style>
    /* ===== ОБЩИЕ СТИЛИ ===== */
    .main-header {
        font-size: 3rem;
        font-weight: 800;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.3rem;
        letter-spacing: -1px;
    }
    
    .subtitle {
        font-size: 1.1rem;
        color: #888;
        margin-bottom: 2rem;
        font-weight: 300;
    }
    
    /* ===== КАРТОЧКИ МЕТРИК ===== */
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem;
        border-radius: 15px;
        color: white;
        margin-bottom: 1rem;
        box-shadow: 0 10px 30px rgba(102, 126, 234, 0.3);
        transition: transform 0.3s ease;
    }
    
    .metric-card:hover {
        transform: translateY(-5px);
    }
    
    .metric-card.success {
        background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
        box-shadow: 0 10px 30px rgba(17, 153, 142, 0.3);
    }
    
    .metric-card.danger {
        background: linear-gradient(135deg, #eb3349 0%, #f45c43 100%);
        box-shadow: 0 10px 30px rgba(235, 51, 73, 0.3);
    }
    
    .metric-card.info {
        background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
        box-shadow: 0 10px 30px rgba(79, 172, 254, 0.3);
    }
    
    .metric-card.warning {
        background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
        box-shadow: 0 10px 30px rgba(245, 87, 108, 0.3);
    }
    
    /* ===== ИНФОРМАЦИОННЫЕ БЛОКИ ===== */
    .info-box {
        background: linear-gradient(135deg, #e0f7fa 0%, #b2ebf2 100%);
        border-left: 5px solid #00bcd4;
        padding: 1.2rem;
        border-radius: 10px;
        margin: 1rem 0;
        box-shadow: 0 4px 15px rgba(0, 188, 212, 0.1);
    }
    
    .success-box {
        background: linear-gradient(135deg, #e8f5e9 0%, #c8e6c9 100%);
        border-left: 5px solid #4caf50;
        padding: 1.2rem;
        border-radius: 10px;
        margin: 1rem 0;
        box-shadow: 0 4px 15px rgba(76, 175, 80, 0.1);
    }
    
    .error-box {
        background: linear-gradient(135deg, #ffebee 0%, #ffcdd2 100%);
        border-left: 5px solid #f44336;
        padding: 1.2rem;
        border-radius: 10px;
        margin: 1rem 0;
        box-shadow: 0 4px 15px rgba(244, 67, 54, 0.1);
    }
    
    /* ===== КНОПКИ ===== */
    .stButton > button {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        padding: 0.8rem 1.5rem;
        border-radius: 10px;
        font-weight: 600;
        transition: all 0.3s ease;
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
    }
    
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(102, 126, 234, 0.4);
    }
    
    /* ===== РАЗДЕЛИТЕЛИ ===== */
    hr {
        border: none;
        height: 2px;
        background: linear-gradient(90deg, transparent, #667eea, transparent);
        margin: 2rem 0;
    }
    
    /* ===== АНИМАЦИЯ ===== */
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(20px); }
        to { opacity: 1; transform: translateY(0); }
    }
    
    .stSuccess, .stError, .stInfo, .stWarning {
        animation: fadeIn 0.5s ease;
    }
    
    /* ===== МОБИЛЬНАЯ АДАПТАЦИЯ ===== */
    @media (max-width: 768px) {
        .main-header {
            font-size: 2rem;
        }
        
        .metric-card {
            padding: 1rem;
        }
    }
</style>
""", unsafe_allow_html=True)

# ===== ШАПКА =====
st.markdown("""
<div style="display: flex; align-items: center; gap: 20px; margin-bottom: 1rem;">
    <div style="font-size: 4rem; filter: drop-shadow(0 4px 8px rgba(102, 126, 234, 0.3));">📐</div>
    <div>
        <div class="main-header">Geogeolevel</div>
        <div class="subtitle">Профессиональная обработка нивелирных ходов · Trimble DiNi</div>
    </div>
</div>
""", unsafe_allow_html=True)

# ===== SIDEBAR =====
with st.sidebar:
    st.header("⚙️ Параметры")
    
    uploaded = st.file_uploader("📂 Загрузите файл .DAT", type=["dat", "txt"])
    
    st.divider()
    
    level_class = st.selectbox(
        "Класс нивелирования",
        options=["technical", "IV", "III", "II", "I"],
        format_func=lambda x: {
            "technical": "Техническое",
            "IV": "IV класс",
            "III": "III класс",
            "II": "II класс",
            "I": "I класс",
        }[x],
        index=0,
        key="class_select"
    )
    
    misclosure_mode = st.radio(
        "Режим расчета допуска",
        options=["distance", "stations"],
        format_func=lambda x: "По длине хода (км)" if x == "distance" else "По числу станций",
        index=0,
        key="misclosure_mode"
    )
    
    st.divider()
    
    h_start_override = st.number_input("H начальной (м, необязательно)", value=None, format="%.3f", key="h_start")
    h_end_override = st.number_input("H конечной (м, необязательно)", value=None, format="%.3f", key="h_end")

# ===== ОСНОВНАЯ ЛОГИКА =====
if not uploaded:
    st.info("👈 Загрузите файл .DAT в панели слева для начала работы")
    st.stop()

try:
    parser = TrimbleDiniParser(uploaded.read())
    job_name, measurements, points_with_heights, scheme = parser.parse()
    
    st.success(f"✅ Файл прочитан: **{job_name}**")
    
    # Показываем схему
    scheme_name = "BBFF (ЗЗПП)" if scheme == "BBFF" else "BFFB (ЗППЗ)"
    st.markdown(f'<div class="info-box">📋 Обнаружена схема измерений: **{scheme_name}**</div>', unsafe_allow_html=True)
    
    analyzer = RunAnalyzer(measurements, points_with_heights)
    stations = analyzer.group_into_stations()
    runs = analyzer.identify_runs(stations)
    
    st.info(f"📊 Найдено: **{len(measurements)}** измерений, **{len(stations)}** станций, **{len(runs)}** ход(ов)")
    
    if points_with_heights:
        with st.expander(" Найденные исходные отметки"):
            for pt, h in sorted(points_with_heights.items()):
                st.text(f"{pt}: {h:.5f} м")
    
    all_results = []
    all_dfs = []
    
    for run in runs:
        h_start = h_start_override if h_start_override is not None else points_with_heights.get(run.start_point, 100.000)
        h_end = h_end_override if h_end_override is not None else points_with_heights.get(run.end_point, None)
        
        engine = LevelingAdjustment(run, h_start, h_end, level_class, misclosure_mode)
        result = engine.calculate()
        all_results.append(result)
        
        if result.get("points"):
            df = pd.DataFrame([{
                "Ход": f"Ход {result['run_id']}",
                "Точка": p.point_id,
                "H исх., м": f"{p.h_original:.5f}" if p.h_original else "—",
                "Поправка, мм": round(p.correction * 1000, 2),
                "H уравн., м": round(p.h_adjusted, 4),
                "Расст., м": round(p.distance_from_start, 1),
            } for p in result["points"]])
            all_dfs.append(df)
    
    # ===== РЕЗУЛЬТАТЫ =====
    st.divider()
    st.subheader("📊 Результаты уравнивания")
    
    for idx, result in enumerate(all_results):
        st.markdown(f"### Ход {result['run_id']}: {result['start_point']} → {result['end_point']}")
        
        # Метрики в карточках
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.markdown(f"""
            <div class="metric-card info">
                <div style="font-size: 0.9rem; opacity: 0.9;">📏 Длина хода</div>
                <div style="font-size: 1.8rem; font-weight: 700;">{result['length_km']:.3f} км</div>
            </div>
            """, unsafe_allow_html=True)
        
        with col2:
            st.markdown(f"""
            <div class="metric-card">
                <div style="font-size: 0.9rem; opacity: 0.9;"> Станций</div>
                <div style="font-size: 1.8rem; font-weight: 700;">{result['n_stations']}</div>
            </div>
            """, unsafe_allow_html=True)
        
        with col3:
            st.markdown(f"""
            <div class="metric-card warning">
                <div style="font-size: 0.9rem; opacity: 0.9;">📐 Невязка fh</div>
                <div style="font-size: 1.8rem; font-weight: 700;">{result['misclosure_mm']:.2f} мм</div>
            </div>
            """, unsafe_allow_html=True)
        
        with col4:
            st.markdown(f"""
            <div class="metric-card info">
                <div style="font-size: 0.9rem; opacity: 0.9;">✅ Допуск</div>
                <div style="font-size: 1.8rem; font-weight: 700;">±{result['allowed_mm']:.2f} мм</div>
            </div>
            """, unsafe_allow_html=True)
        
        # Формула
        st.markdown(f"""
        <div class="metric-card info">
            <div style="font-size: 0.9rem; opacity: 0.9; margin-bottom: 0.5rem;">📐 Формула допуска</div>
            <div style="font-size: 1.3rem; font-weight: 700;">{result['formula_used']}</div>
        </div>
        """, unsafe_allow_html=True)
        
        # Статус
        if result["is_valid"]:
            st.markdown('<div class="success-box">✅ Невязка в допуске — ход уравнен</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="error-box">🚨 {result["error"]}</div>', unsafe_allow_html=True)
        
        # Таблица
        if idx < len(all_dfs):
            st.dataframe(all_dfs[idx], use_container_width=True, hide_index=True)
        
        # График
        if result.get("points"):
            fig = go.Figure()
            
            distances = [p.distance_from_start for p in result["points"]]
            heights = [p.h_adjusted for p in result["points"]]
            point_ids = [p.point_id for p in result["points"]]
            
            fig.add_trace(go.Scatter(
                x=distances, y=heights,
                mode="lines+markers+text",
                text=point_ids,
                textposition="top center",
                name=f"Ход {result['run_id']}",
                line=dict(color="#667eea", width=3),
                marker=dict(size=12, color="#764ba2")
            ))
            
            fig.update_layout(
                title=f"Профиль хода {result['run_id']}",
                xaxis_title="Расстояние от начала, м",
                yaxis_title="Отметка, м",
                height=400,
                template="plotly_white",
                showlegend=False
            )
            st.plotly_chart(fig, use_container_width=True)
    
    # ===== ЭКСПОРТ =====
    if all_dfs:
        st.divider()
        st.subheader("💾 Экспорт результатов")
        
        combined_df = pd.concat(all_dfs, ignore_index=True)
        
        col_a, col_b = st.columns(2)
        
        csv = combined_df.to_csv(index=False).encode('utf-8-sig')
        col_a.download_button(
            "📥 Скачать CSV",
            data=csv,
            file_name=f"{job_name}_report.csv",
            mime="text/csv",
            use_container_width=True
        )
        
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
            combined_df.to_excel(writer, sheet_name='Отчёт', index=False)
        col_b.download_button(
            "📥 Скачать Excel",
            data=buffer.getvalue(),
            file_name=f"{job_name}_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True
        )

except Exception as e:
    st.error(f"❌ Ошибка обработки: {e}")
    import traceback
    st.code(traceback.format_exc())

# ===== ФУТЕР =====
st.divider()
st.markdown(f"""
<div style="display: flex; justify-content: space-between; color: #888; font-size: 0.9rem; padding: 1rem 0;">
    <span>📐 Geogeolevel v5.0</span>
    <span>📅 {datetime.now().strftime('%d.%m.%Y')}</span>
    <span>⚡ Streamlit Cloud</span>
</div>
""", unsafe_allow_html=True)
