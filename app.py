"""
Geogeolevel v9.0 - Multi-format + Ведомость + Статистика + Накопленная невязка + Редактор
"""

import io
import math
import os
import re
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Tuple, Set

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer


# ============================================================
# МОДЕЛИ ДАННЫХ
# ============================================================

@dataclass
class RawReading:
    point: str
    reading: float
    distance: float
    reading_type: str


@dataclass
class RawMeasurement:
    back_point: str
    fore_point: str
    back_reading: float
    fore_reading: float
    back_dist: float
    fore_dist: float


@dataclass
class Station:
    station_id: int
    back_point: str
    fore_point: str
    dh_mean: float
    distance: float
    dh_1: float
    dh_2: float
    dist_1: float
    dist_2: float
    dh_diff: float
    dist_diff: float
    measurements: List[RawMeasurement] = field(default_factory=list)


@dataclass
class LevelingRun:
    run_id: int
    stations: List[Station]
    start_point: str
    end_point: str
    is_closed: bool


@dataclass
class AdjustedPoint:
    point_id: str
    h_original: Optional[float]
    h_adjusted: float
    correction: float
    distance_from_start: float


@dataclass
class QualityIssue:
    station_id: int
    point_from: str
    point_to: str
    issue_type: str
    value: float
    limit: float
    severity: str
    message: str


# ============================================================
# ПАРСЕРЫ
# ============================================================

class TrimbleDiniParser:
    def __init__(self, raw_bytes: bytes):
        self.raw_bytes = raw_bytes

    def parse(self) -> Tuple[str, List[RawMeasurement], Dict[str, float], str]:
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
        
        scheme = self._detect_scheme(readings)
        measurements = self._group_readings(readings, scheme)
        
        return job_name, measurements, points_with_heights, scheme

    def _detect_scheme(self, readings: List[RawReading]) -> str:
        for i in range(len(readings) - 1):
            if readings[i].reading_type == 'Rb':
                if readings[i + 1].reading_type == 'Rb':
                    return 'BBFF'
                elif readings[i + 1].reading_type == 'Rf':
                    return 'BFFB'
        return 'BFFB'

    def _group_readings(self, readings: List[RawReading], scheme: str) -> List[RawMeasurement]:
        measurements = []
        i = 0
        
        while i < len(readings):
            if scheme == 'BBFF':
                if i + 3 < len(readings):
                    if (readings[i].reading_type == 'Rb' and 
                        readings[i+1].reading_type == 'Rb' and
                        readings[i+2].reading_type == 'Rf' and 
                        readings[i+3].reading_type == 'Rf'):
                        
                        measurements.append(RawMeasurement(
                            back_point=readings[i].point,
                            fore_point=readings[i+2].point,
                            back_reading=readings[i].reading,
                            fore_reading=readings[i+2].reading,
                            back_dist=readings[i].distance,
                            fore_dist=readings[i+2].distance
                        ))
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
                if i + 3 < len(readings):
                    if (readings[i].reading_type == 'Rb' and 
                        readings[i+1].reading_type == 'Rf' and
                        readings[i+2].reading_type == 'Rf' and 
                        readings[i+3].reading_type == 'Rb'):
                        
                        measurements.append(RawMeasurement(
                            back_point=readings[i].point,
                            fore_point=readings[i+1].point,
                            back_reading=readings[i].reading,
                            fore_reading=readings[i+1].reading,
                            back_dist=readings[i].distance,
                            fore_dist=readings[i+1].distance
                        ))
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


class LeicaGSIParser:
    """Парсер Leica GSI-16/GSI-32"""
    
    def __init__(self, raw_bytes: bytes):
        self.raw_bytes = raw_bytes

    def parse(self) -> Tuple[str, List[RawMeasurement], Dict[str, float], str]:
        text = self._decode_text()
        lines = text.splitlines()
        
        measurements = []
        points_with_heights = {}
        job_name = "Leica GSI"
        
        # Извлекаем имя работы
        for line in lines[:10]:
            if 'JOB' in line.upper() or 'GSI' in line.upper():
                match = re.search(r'JOB[:\s]+(\S+)', line, re.IGNORECASE)
                if match:
                    job_name = match.group(1)
                break
        
        # Парсим GSI записи
        current_back_point = None
        current_back_reading = None
        current_back_dist = None
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Ищем GSI записи (16 символов каждая)
            # Формат: CC IIIII VVVVVVVV S
            # C - код (2 цифры), I - индекс (5 цифр), V - значение (8 цифр), S - знак (1 символ)
            gsi_pattern = r'(\d{2})(\d{5})([+\-]?\d{8})([+\-])'
            
            for match in re.finditer(gsi_pattern, line):
                code = match.group(1)
                value_str = match.group(3) + match.group(4)
                value = int(value_str)
                
                # Код 11 - имя точки
                if code == '11':
                    point_name = line[match.start():match.end()].strip()
                    # Извлекаем имя точки из контекста
                    continue
                
                # Код 21 - отсчёт по задней рейке (чёрная сторона) в 0.1 мм
                elif code == '21':
                    reading = value / 10000.0  # 0.1 мм -> м
                    # Ищем имя точки в этой же записи
                    point_match = re.search(r'11\d{5}(\S+)', line[:match.start()])
                    if point_match:
                        current_back_point = point_match.group(1)
                        current_back_reading = reading
                    continue
                
                # Код 22 - отсчёт по передней рейке
                elif code == '22':
                    reading = value / 10000.0
                    continue
                
                # Код 31 - расстояние до задней рейки в мм
                elif code == '31':
                    dist = value / 1000.0  # мм -> м
                    if current_back_point:
                        current_back_dist = dist
                    continue
                
                # Код 32 - расстояние до передней рейки
                elif code == '32':
                    continue
                
                # Код 41 - превышение (чёрная сторона) в 0.1 мм
                elif code == '41':
                    dh = value / 10000.0
                    continue
        
        # Если GSI парсинг не дал результатов, пробуем упрощённый формат
        if not measurements:
            measurements, points_with_heights = self._parse_simple_gsi(lines)
        
        return job_name, measurements, points_with_heights, 'GSI'

    def _parse_simple_gsi(self, lines: List[str]) -> Tuple[List[RawMeasurement], Dict[str, float]]:
        """Упрощённый парсер для GSI-подобных форматов"""
        measurements = []
        points_with_heights = {}
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Ищем отметки точек
            z_match = re.search(r'Z\s*[:=]?\s*([+-]?\d+\.?\d*)', line, re.IGNORECASE)
            if z_match:
                point_match = re.search(r'(?:Point|ID|Name|11)\s*[:=]?\s*(\S+)', line, re.IGNORECASE)
                if point_match:
                    points_with_heights[point_match.group(1)] = float(z_match.group(1))
                continue
            
            # Ищем отсчёты по рейкам
            rb_match = re.search(r'(?:Rb|BS|Back)\s*[:=]?\s*([+-]?\d+\.?\d*)', line, re.IGNORECASE)
            rf_match = re.search(r'(?:Rf|FS|Fore)\s*[:=]?\s*([+-]?\d+\.?\d*)', line, re.IGNORECASE)
            dist_match = re.search(r'(?:Dist|HD|Len)\s*[:=]?\s*([+-]?\d+\.?\d*)', line, re.IGNORECASE)
            point_match = re.search(r'(?:Point|ID|Name|11)\s*[:=]?\s*(\S+)', line, re.IGNORECASE)
            
            if rb_match and rf_match and point_match:
                measurements.append(RawMeasurement(
                    back_point=point_match.group(1),
                    fore_point=point_match.group(1),
                    back_reading=float(rb_match.group(1)),
                    fore_reading=float(rf_match.group(1)),
                    back_dist=float(dist_match.group(1)) if dist_match else 0,
                    fore_dist=float(dist_match.group(1)) if dist_match else 0
                ))
        
        return measurements, points_with_heights

    def _decode_text(self) -> str:
        for enc in ['utf-8', 'windows-1251', 'cp1251', 'latin-1']:
            try:
                return self.raw_bytes.decode(enc)
            except UnicodeDecodeError:
                continue
        return self.raw_bytes.decode('latin-1', errors='ignore')


class SokkiaTopconParser:
    """Парсер Sokkia/Topcon CSV"""
    
    def __init__(self, raw_bytes: bytes):
        self.raw_bytes = raw_bytes

    def parse(self) -> Tuple[str, List[RawMeasurement], Dict[str, float], str]:
        text = self._decode_text()
        lines = text.splitlines()
        
        measurements = []
        points_with_heights = {}
        job_name = "Sokkia/Topcon"
        
        if not lines:
            return job_name, measurements, points_with_heights, 'CSV'
        
        # Определяем разделитель
        header = lines[0]
        if '\t' in header:
            separator = '\t'
        elif ';' in header:
            separator = ';'
        else:
            separator = ','
        
        # Парсим заголовок
        headers = [h.strip().lower() for h in header.split(separator)]
        
        col_map = {}
        for i, h in enumerate(headers):
            if any(k in h for k in ['point', 'name', 'id', 'точка']):
                col_map['point'] = i
            elif any(k in h for k in ['back', 'bs', 'rear', 'задн']):
                col_map['back'] = i
            elif any(k in h for k in ['fore', 'fs', 'front', 'перед']):
                col_map['fore'] = i
            elif any(k in h for k in ['dist', 'len', 'расст']):
                col_map['dist'] = i
            elif any(k in h for k in ['dh', 'height', 'elev', 'h ', 'отмет']):
                col_map['height'] = i
        
        # Парсим данные
        prev_point = None
        for line in lines[1:]:
            line = line.strip()
            if not line:
                continue
            
            parts = [p.strip() for p in line.split(separator)]
            
            try:
                if 'point' in col_map and 'back' in col_map and 'fore' in col_map:
                    point = parts[col_map['point']]
                    back = float(parts[col_map['back']].replace(',', '.'))
                    fore = float(parts[col_map['fore']].replace(',', '.'))
                    dist = float(parts[col_map['dist']].replace(',', '.')) if 'dist' in col_map else 0
                    
                    if prev_point is None:
                        prev_point = point
                        continue
                    
                    measurements.append(RawMeasurement(
                        back_point=prev_point,
                        fore_point=point,
                        back_reading=back,
                        fore_reading=fore,
                        back_dist=dist,
                        fore_dist=dist
                    ))
                    prev_point = point
                
                if 'height' in col_map and 'point' in col_map:
                    point = parts[col_map['point']]
                    height = float(parts[col_map['height']].replace(',', '.'))
                    points_with_heights[point] = height
                    
            except (ValueError, IndexError):
                continue
        
        return job_name, measurements, points_with_heights, 'CSV'

    def _decode_text(self) -> str:
        for enc in ['utf-8', 'windows-1251', 'cp1251', 'latin-1']:
            try:
                return self.raw_bytes.decode(enc)
            except UnicodeDecodeError:
                continue
        return self.raw_bytes.decode('latin-1', errors='ignore')


class UniversalCSVParser:
    """Универсальный парсер CSV с автоопределением формата"""
    
    def __init__(self, raw_bytes: bytes):
        self.raw_bytes = raw_bytes

    def parse(self) -> Tuple[str, List[RawMeasurement], Dict[str, float], str]:
        text = self._decode_text()
        lines = text.splitlines()
        
        measurements = []
        points_with_heights = {}
        job_name = "Universal CSV"
        
        if not lines:
            return job_name, measurements, points_with_heights, 'CSV'
        
        # Определяем разделитель
        header = lines[0]
        if '\t' in header:
            separator = '\t'
        elif ';' in header:
            separator = ';'
        else:
            separator = ','
        
        headers = [h.strip().lower() for h in header.split(separator)]
        
        # Автоопределение колонок
        col_map = {}
        for i, h in enumerate(headers):
            h_lower = h.lower()
            if any(k in h_lower for k in ['point', 'name', 'id', 'точка', 'pt']):
                col_map['point'] = i
            elif any(k in h_lower for k in ['back', 'bs', 'rear', 'задн', 'rb']):
                col_map['back'] = i
            elif any(k in h_lower for k in ['fore', 'fs', 'front', 'перед', 'rf']):
                col_map['fore'] = i
            elif any(k in h_lower for k in ['dist', 'len', 'расст', 'hd']):
                col_map['dist'] = i
            elif any(k in h_lower for k in ['dh', 'height', 'elev', 'h ', 'отмет', 'z']):
                col_map['height'] = i
        
        # Если не нашли стандартные колонки, пробуем числовой формат
        if 'back' not in col_map or 'fore' not in col_map:
            return self._parse_numeric_format(lines, separator)
        
        # Парсим данные
        prev_point = None
        for line in lines[1:]:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            parts = [p.strip() for p in line.split(separator)]
            
            try:
                if 'point' in col_map and 'back' in col_map and 'fore' in col_map:
                    point = parts[col_map['point']]
                    back = float(parts[col_map['back']].replace(',', '.'))
                    fore = float(parts[col_map['fore']].replace(',', '.'))
                    dist = float(parts[col_map['dist']].replace(',', '.')) if 'dist' in col_map else 0
                    
                    if prev_point is None:
                        prev_point = point
                        continue
                    
                    measurements.append(RawMeasurement(
                        back_point=prev_point,
                        fore_point=point,
                        back_reading=back,
                        fore_reading=fore,
                        back_dist=dist,
                        fore_dist=dist
                    ))
                    prev_point = point
                
                if 'height' in col_map and 'point' in col_map:
                    point = parts[col_map['point']]
                    height = float(parts[col_map['height']].replace(',', '.'))
                    points_with_heights[point] = height
                    
            except (ValueError, IndexError):
                continue
        
        return job_name, measurements, points_with_heights, 'CSV'

    def _parse_numeric_format(self, lines: List[str], separator: str) -> Tuple[str, List[RawMeasurement], Dict[str, float], str]:
        """Парсинг числового формата: Point, dH, Dist"""
        measurements = []
        points_with_heights = {}
        
        prev_point = None
        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            parts = [p.strip() for p in line.split(separator)]
            if len(parts) < 2:
                continue
            
            try:
                numbers = []
                strings = []
                for p in parts:
                    try:
                        numbers.append(float(p.replace(',', '.')))
                    except ValueError:
                        strings.append(p)
                
                if strings and len(numbers) >= 2:
                    point_id = strings[0]
                    dh, dist = numbers[0], numbers[1]
                    
                    if dh > 10 and dist < 10:
                        dh, dist = dist, dh
                    
                    if prev_point is None:
                        prev_point = point_id
                        continue
                    
                    measurements.append(RawMeasurement(
                        back_point=prev_point,
                        fore_point=point_id,
                        back_reading=dh,
                        fore_reading=0,
                        back_dist=dist,
                        fore_dist=0
                    ))
                    prev_point = point_id
                    
            except (ValueError, IndexError):
                continue
        
        return "Universal", measurements, points_with_heights, 'CSV'

    def _decode_text(self) -> str:
        for enc in ['utf-8', 'windows-1251', 'cp1251', 'latin-1']:
            try:
                return self.raw_bytes.decode(enc)
            except UnicodeDecodeError:
                continue
        return self.raw_bytes.decode('latin-1', errors='ignore')


def detect_and_parse(raw_bytes: bytes, filename: str) -> Tuple[str, List[RawMeasurement], Dict[str, float], str]:
    """Автоопределение формата и парсинг"""
    filename_lower = filename.lower()
    
    # По расширению/имени файла
    if 'gsi' in filename_lower or filename_lower.endswith('.gsi'):
        try:
            parser = LeicaGSIParser(raw_bytes)
            return parser.parse()
        except:
            pass
    
    if filename_lower.endswith(('.csv', '.txt')):
        # Проверяем, Sokkia/Topcon или универсальный
        text = raw_bytes.decode('utf-8', errors='ignore')
        first_line = text.split('\n')[0].lower()
        
        if any(k in first_line for k in ['sokkia', 'topcon', 'bs,', 'fs,']):
            try:
                parser = SokkiaTopconParser(raw_bytes)
                return parser.parse()
            except:
                pass
        
        try:
            parser = UniversalCSVParser(raw_bytes)
            return parser.parse()
        except:
            pass
    
    # По умолчанию Trimble DiNi
    parser = TrimbleDiniParser(raw_bytes)
    return parser.parse()


# ============================================================
# АНАЛИЗАТОР
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
            
            dh_1 = dh_values[0] if len(dh_values) > 0 else 0
            dh_2 = dh_values[1] if len(dh_values) > 1 else dh_1
            dist_1 = dist_values[0] if len(dist_values) > 0 else 0
            dist_2 = dist_values[1] if len(dist_values) > 1 else dist_1
            
            stations.append(Station(
                station_id=idx,
                back_point=back_pt,
                fore_point=fore_pt,
                dh_mean=dh_mean,
                distance=dist_mean,
                dh_1=dh_1,
                dh_2=dh_2,
                dist_1=dist_1,
                dist_2=dist_2,
                dh_diff=abs(dh_1 - dh_2) * 1000 if len(dh_values) > 1 else 0,
                dist_diff=abs(dist_1 - dist_2) if len(dist_values) > 1 else 0,
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
                    is_closed = ordered_stations[0].back_point == ordered_stations[-1].fore_point
                    runs.append(LevelingRun(
                        run_id=run_id,
                        stations=ordered_stations,
                        start_point=ordered_stations[0].back_point,
                        end_point=ordered_stations[-1].fore_point,
                        is_closed=is_closed
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
# КОНТРОЛЬ КАЧЕСТВА
# ============================================================

class QualityChecker:
    def __init__(self, run: LevelingRun, level_class: str):
        self.run = run
        self.level_class = level_class
        self.issues: List[QualityIssue] = []
    
    def check_all(self) -> List[QualityIssue]:
        self.issues = []
        self._check_dh_differences()
        self._check_dist_differences()
        return self.issues
    
    def _check_dh_differences(self):
        limits = {"I": 1.0, "II": 2.0, "III": 3.0, "IV": 5.0, "technical": 10.0}
        limit = limits.get(self.level_class, 5.0)
        
        for station in self.run.stations:
            if station.dh_diff > limit:
                self.issues.append(QualityIssue(
                    station_id=station.station_id,
                    point_from=station.back_point,
                    point_to=station.fore_point,
                    issue_type='dh_diff',
                    value=station.dh_diff,
                    limit=limit,
                    severity='error',
                    message=f"Разность превышений {station.dh_diff:.2f} мм > допуск {limit} мм"
                ))
    
    def _check_dist_differences(self):
        limits = {"I": 2.0, "II": 5.0, "III": 10.0, "IV": 20.0, "technical": 30.0}
        limit = limits.get(self.level_class, 10.0)
        
        for station in self.run.stations:
            if station.dist_diff > limit:
                self.issues.append(QualityIssue(
                    station_id=station.station_id,
                    point_from=station.back_point,
                    point_to=station.fore_point,
                    issue_type='dist_diff',
                    value=station.dist_diff,
                    limit=limit,
                    severity='warning',
                    message=f"Разность плеч {station.dist_diff:.1f} м > допуск {limit} м"
                ))
    
    def check_anomalies(self, adjusted_points: List[AdjustedPoint], sum_dist: float):
        if not adjusted_points or sum_dist == 0:
            return
        
        avg_correction_per_km = sum(abs(p.correction) for p in adjusted_points) / (sum_dist / 1000.0)
        
        for point in adjusted_points:
            if point.correction == 0:
                continue
            if abs(point.correction) > 5 * avg_correction_per_km * (point.distance_from_start / sum_dist) and abs(point.correction) > 0.001:
                self.issues.append(QualityIssue(
                    station_id=0,
                    point_from="",
                    point_to=point.point_id,
                    issue_type='anomaly',
                    value=abs(point.correction) * 1000,
                    limit=5 * avg_correction_per_km * 1000,
                    severity='warning',
                    message=f"Аномальная поправка в точке {point.point_id}: {point.correction*1000:.2f} мм"
                ))


# ============================================================
# УРАВНИВАТЕЛЬ
# ============================================================

class LevelingAdjustment:
    def __init__(self, run: LevelingRun, h_start: float, h_end: Optional[float],
                 level_class: str = "technical", misclosure_mode: str = "distance",
                 use_mnk: bool = False):
        self.run = run
        self.h_start = h_start
        self.h_end = h_end if h_end is not None else h_start
        self.level_class = level_class
        self.misclosure_mode = misclosure_mode
        self.use_mnk = use_mnk

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

        if is_valid:
            points = self._distribute_corrections(misclosure_m, sum_dist)
            cumulative_misclosure = self._calculate_cumulative_misclosure(misclosure_m, sum_dist)
        else:
            points = []
            cumulative_misclosure = []
        
        checker = QualityChecker(self.run, self.level_class)
        checker.check_all()
        if points:
            checker.check_anomalies(points, sum_dist)

        stats = self._calculate_statistics()

        return {
            "run_id": self.run.run_id,
            "start_point": self.run.start_point,
            "end_point": self.run.end_point,
            "is_closed": self.run.is_closed,
            "sum_dh_m": round(sum_dh, 4),
            "dh_theoretical_m": round(dh_theoretical, 4),
            "misclosure_mm": round(misclosure_mm, 2),
            "allowed_mm": round(allowed_mm, 2),
            "length_km": round(length_km, 3),
            "n_stations": n_stations,
            "is_valid": is_valid,
            "points": points,
            "level_class": self.level_class,
            "formula_used": self._get_formula_text(length_km, n_stations),
            "error": f"Невязка {misclosure_mm:.2f} мм превышает допуск {allowed_mm:.2f} мм!" if not is_valid else None,
            "quality_issues": checker.issues,
            "method": "МНК" if self.use_mnk else "Пропорционально",
            "cumulative_misclosure": cumulative_misclosure,
            "stats": stats
        }

    def _calculate_cumulative_misclosure(self, misclosure_m: float, sum_dist: float) -> List[Dict]:
        """Расчёт накопленной невязки по станциям"""
        result = []
        cumulative = 0.0
        cumulative_dist = 0.0
        
        for station in self.run.stations:
            theoretical_dh = (self.h_end - self.h_start) * (station.distance / sum_dist)
            cumulative += (station.dh_mean - theoretical_dh)
            cumulative_dist += station.distance
            
            result.append({
                "station_id": station.station_id,
                "point": station.fore_point,
                "cumulative_mm": round(cumulative * 1000, 2),
                "distance": round(cumulative_dist, 1)
            })
        
        return result

    def _calculate_statistics(self) -> Dict:
        """Статистика по ходу"""
        if not self.run.stations:
            return {}
        
        dh_values = [s.dh_mean for s in self.run.stations]
        dist_values = [s.distance for s in self.run.stations]
        dh_diffs = [s.dh_diff for s in self.run.stations if s.dh_diff > 0]
        dist_diffs = [s.dist_diff for s in self.run.stations if s.dist_diff > 0]
        
        return {
            "min_dh": round(min(dh_values), 4),
            "max_dh": round(max(dh_values), 4),
            "mean_dh": round(sum(dh_values) / len(dh_values), 4),
            "mean_dist": round(sum(dist_values) / len(dist_values), 1),
            "max_dist_diff": round(max(dist_diffs), 1) if dist_diffs else 0,
            "stations_in_tolerance": len([s for s in self.run.stations if s.dh_diff <= 2.0]),
            "total_stations": len(self.run.stations)
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
# ЭКСПОРТ PDF
# ============================================================

class PDFExporter:
    @staticmethod
    def _download_font(font_path: str, url: str) -> bool:
        if os.path.exists(font_path) and os.path.getsize(font_path) > 10000:
            return True
        try:
            urllib.request.urlretrieve(url, font_path)
            return os.path.exists(font_path) and os.path.getsize(font_path) > 10000
        except:
            return False
    
    @staticmethod
    def create_report(result: dict, job_name: str, level_class: str) -> bytes:
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, 
                               rightMargin=15*mm, leftMargin=15*mm,
                               topMargin=15*mm, bottomMargin=25*mm)
        
        elements = []
        styles = getSampleStyleSheet()
        
        font_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
        os.makedirs(font_dir, exist_ok=True)
        
        font_regular = os.path.join(font_dir, "DejaVuSans.ttf")
        font_bold = os.path.join(font_dir, "DejaVuSans-Bold.ttf")
        
        PDFExporter._download_font(font_regular, 
            "https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/DejaVuSans.ttf")
        PDFExporter._download_font(font_bold,
            "https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/DejaVuSans-Bold.ttf")
        
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        
        use_cyrillic = False
        if os.path.exists(font_regular) and os.path.getsize(font_regular) > 10000:
            try:
                pdfmetrics.registerFont(TTFont('DejaVu', font_regular))
                pdfmetrics.registerFont(TTFont('DejaVu-Bold', font_bold))
                use_cyrillic = True
            except:
                pass
        
        title_style = ParagraphStyle('Title', parent=styles['Heading1'], 
                                    fontName='DejaVu-Bold' if use_cyrillic else 'Helvetica-Bold', 
                                    fontSize=14, alignment=1, spaceAfter=10)
        heading_style = ParagraphStyle('Heading', parent=styles['Heading2'],
                                      fontName='DejaVu-Bold' if use_cyrillic else 'Helvetica-Bold',
                                      fontSize=11, spaceAfter=6)
        normal_style = ParagraphStyle('Normal', parent=styles['Normal'],
                                     fontName='DejaVu' if use_cyrillic else 'Helvetica',
                                     fontSize=9)
        
        elements.append(Paragraph("ОТЧЁТ ПО НИВЕЛИРНОМУ ХОДУ" if use_cyrillic else "LEVELING REPORT", title_style))
        elements.append(Spacer(1, 5*mm))
        
        info_data = [
            ["Работа:" if use_cyrillic else "Job:", job_name],
            ["Дата:" if use_cyrillic else "Date:", datetime.now().strftime('%d.%m.%Y %H:%M')],
            ["Класс:" if use_cyrillic else "Class:", level_class.upper()],
            ["Метод:" if use_cyrillic else "Method:", result.get('method', '')],
        ]
        
        info_table = Table(info_data, colWidths=[35*mm, 100*mm])
        info_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'DejaVu-Bold' if use_cyrillic else 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'DejaVu' if use_cyrillic else 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 5*mm))
        
        elements.append(Paragraph("ПАРАМЕТРЫ ХОДА" if use_cyrillic else "RUN PARAMETERS", heading_style))
        
        params_data = [
            ["Длина хода:" if use_cyrillic else "Length:", f"{result['length_km']:.3f} км" if use_cyrillic else f"{result['length_km']:.3f} km"],
            ["Станций:" if use_cyrillic else "Stations:", str(result['n_stations'])],
            ["Невязка:" if use_cyrillic else "Misclosure:", f"{result['misclosure_mm']:.2f} мм" if use_cyrillic else f"{result['misclosure_mm']:.2f} mm"],
            ["Допуск:" if use_cyrillic else "Tolerance:", f"±{result['allowed_mm']:.2f} мм" if use_cyrillic else f"±{result['allowed_mm']:.2f} mm"],
        ]
        
        params_table = Table(params_data, colWidths=[45*mm, 90*mm])
        params_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'DejaVu-Bold' if use_cyrillic else 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'DejaVu' if use_cyrillic else 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(params_table)
        elements.append(Spacer(1, 3*mm))
        
        formula_text = f"Формула: {result['formula_used']}" if use_cyrillic else f"Formula: {result['formula_used']}"
        elements.append(Paragraph(formula_text, normal_style))
        elements.append(Spacer(1, 5*mm))
        
        if result['is_valid']:
            status = "НЕВЯЗКА В ДОПУСКЕ — ХОД УРАВНЕН" if use_cyrillic else "MISCLOSURE WITHIN TOLERANCE"
            status_style = ParagraphStyle('Status', parent=normal_style, 
                                         textColor=colors.green, fontName='DejaVu-Bold' if use_cyrillic else 'Helvetica-Bold')
        else:
            status = result['error']
            status_style = ParagraphStyle('Status', parent=normal_style,
                                         textColor=colors.red, fontName='DejaVu-Bold' if use_cyrillic else 'Helvetica-Bold')
        elements.append(Paragraph(status, status_style))
        elements.append(Spacer(1, 8*mm))
        
        elements.append(Paragraph("ВЕДОМОСТЬ УРАВНЕННЫХ ОТМЕТОК" if use_cyrillic else "ADJUSTED HEIGHTS TABLE", heading_style))
        elements.append(Spacer(1, 3*mm))
        
        table_data = [["Точка" if use_cyrillic else "Point", 
                      "H исх., м" if use_cyrillic else "H orig., m",
                      "Поправка, мм" if use_cyrillic else "Correction, mm",
                      "H уравн., м" if use_cyrillic else "H adj., m",
                      "Расст., м" if use_cyrillic else "Dist., m"]]
        
        for point in result['points']:
            h_orig = f"{point.h_original:.5f}" if point.h_original else "—"
            table_data.append([
                point.point_id,
                h_orig,
                f"{point.correction*1000:.2f}",
                f"{point.h_adjusted:.4f}",
                f"{point.distance_from_start:.1f}"
            ])
        
        data_table = Table(table_data, colWidths=[25*mm, 35*mm, 35*mm, 35*mm, 35*mm])
        data_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'DejaVu-Bold' if use_cyrillic else 'Helvetica-Bold'),
            ('FONTNAME', (0, 1), (-1, -1), 'DejaVu' if use_cyrillic else 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ALIGN', (1, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(data_table)
        
        doc.build(elements)
        return buffer.getvalue()


# ============================================================
# ИНТЕРФЕЙС
# ============================================================

st.set_page_config(page_title="Geogeolevel", page_icon="📐", layout="wide")

st.markdown("""
<style>
    body {
        background: linear-gradient(135deg, #0f1419 0%, #1a1f2e 100%);
        color: #e7e9ea;
    }
    
    .main-header {
        font-size: 2.8rem;
        font-weight: 800;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.3rem;
        letter-spacing: -1px;
    }
    
    .subtitle {
        font-size: 1rem;
        color: #8b98a5;
        margin-bottom: 1.5rem;
        font-weight: 300;
    }
    
    .kpi-card {
        background: linear-gradient(135deg, rgba(102, 126, 234, 0.15) 0%, rgba(118, 75, 162, 0.15) 100%);
        border: 1px solid rgba(102, 126, 234, 0.3);
        border-radius: 12px;
        padding: 1.2rem;
        margin-bottom: 1rem;
        backdrop-filter: blur(10px);
    }
    
    .kpi-value {
        font-size: 2rem;
        font-weight: 700;
        color: #667eea;
        margin: 0.5rem 0;
    }
    
    .kpi-label {
        font-size: 0.85rem;
        color: #8b98a5;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    
    .glass-card {
        background: rgba(255, 255, 255, 0.05);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(255, 255, 255, 0.1);
        border-radius: 15px;
        padding: 1.5rem;
        margin-bottom: 1rem;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        transition: all 0.3s ease;
    }
    
    .glass-card:hover {
        transform: translateY(-3px);
        box-shadow: 0 12px 40px rgba(102, 126, 234, 0.2);
        border-color: rgba(102, 126, 234, 0.3);
    }
    
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background: rgba(255, 255, 255, 0.05);
        padding: 8px;
        border-radius: 12px;
        border: 1px solid rgba(255, 255, 255, 0.1);
    }
    
    .stTabs [data-baseweb="tab"] {
        background: transparent;
        color: #8b98a5;
        border-radius: 8px;
        padding: 12px 20px;
        font-weight: 600;
        transition: all 0.3s ease;
    }
    
    .stTabs [data-baseweb="tab"][aria-selected="true"] {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.4);
    }
    
    .stButton > button {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border: none;
        padding: 0.8rem 1.5rem;
        border-radius: 10px;
        font-weight: 600;
        transition: all 0.3s ease;
        box-shadow: 0 4px 15px rgba(102, 126, 234, 0.3);
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    
    .stButton > button:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 20px rgba(102, 126, 234, 0.5);
    }
    
    .info-box {
        background: rgba(0, 188, 212, 0.1);
        border-left: 4px solid #00bcd4;
        padding: 1rem;
        border-radius: 8px;
        margin: 1rem 0;
        backdrop-filter: blur(10px);
    }
    
    .success-box {
        background: rgba(76, 175, 80, 0.1);
        border-left: 4px solid #4caf50;
        padding: 1rem;
        border-radius: 8px;
        margin: 1rem 0;
        backdrop-filter: blur(10px);
    }
    
    .error-box {
        background: rgba(244, 67, 54, 0.1);
        border-left: 4px solid #f44336;
        padding: 1rem;
        border-radius: 8px;
        margin: 1rem 0;
        backdrop-filter: blur(10px);
    }
    
    .warning-box {
        background: rgba(255, 152, 0, 0.1);
        border-left: 4px solid #ff9800;
        padding: 1rem;
        border-radius: 8px;
        margin: 1rem 0;
        backdrop-filter: blur(10px);
    }
    
    hr {
        border: none;
        height: 1px;
        background: linear-gradient(90deg, transparent, rgba(102, 126, 234, 0.5), transparent);
        margin: 1.5rem 0;
    }
    
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(10px); }
        to { opacity: 1; transform: translateY(0); }
    }
    
    .stSuccess, .stError, .stInfo, .stWarning {
        animation: fadeIn 0.4s ease;
    }
    
    @media (max-width: 768px) {
        .main-header { font-size: 2rem; }
        .kpi-value { font-size: 1.5rem; }
    }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div style="display: flex; align-items: center; gap: 20px; margin-bottom: 1rem;">
    <div style="font-size: 3.5rem; filter: drop-shadow(0 4px 8px rgba(102, 126, 234, 0.3));"></div>
    <div style="flex: 1;">
        <div class="main-header">Geogeolevel</div>
        <div class="subtitle">Professional Leveling Processing System · Multi-Format</div>
    </div>
</div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ Параметры")
    
    uploaded = st.file_uploader("📂 Загрузите файл", type=["dat", "txt", "csv", "gsi"])
    
    st.divider()
    
    level_class = st.selectbox(
        "Класс нивелирования",
        options=["technical", "IV", "III", "II", "I"],
        format_func=lambda x: {
            "technical": "Техническое", "IV": "IV класс", "III": "III класс",
            "II": "II класс", "I": "I класс",
        }[x],
        index=0, key="class_select"
    )
    
    misclosure_mode = st.radio(
        "Режим расчета допуска",
        options=["distance", "stations"],
        format_func=lambda x: " По длине хода (км)" if x == "distance" else "🔢 По числу станций",
        index=0, key="misclosure_mode"
    )
    
    use_mnk = st.checkbox("🔬 Использовать МНК уравнивание", value=False, key="mnk_checkbox")
    
    st.divider()
    
    h_start_override = st.number_input("H начальной (м, необязательно)", value=None, format="%.3f", key="h_start")
    h_end_override = st.number_input("H конечной (м, необязательно)", value=None, format="%.3f", key="h_end")

if not uploaded:
    st.info(" Загрузите файл в панели слева для начала работы")
    st.stop()

try:
    job_name, measurements, points_with_heights, format_type = detect_and_parse(uploaded.read(), uploaded.name)
    
    st.success(f"✅ Файл прочитан: **{job_name}** (формат: {format_type})")
    
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
        
        engine = LevelingAdjustment(run, h_start, h_end, level_class, misclosure_mode, use_mnk)
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
    
    tab_overview, tab ведомость, tab_profile, tab_quality, tab_editor, tab_export = st.tabs([
        "📊 Обзор", "📋 Ведомость", "📈 Профиль", " Качество", "✏️ Редактор", "💾 Экспорт"
    ])
    
    with tab_overview:
        st.subheader(" Обзор результатов")
        
        if all_results:
            result = all_results[0]
            stats = result.get("stats", {})
            
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.markdown(f"""
                <div class="kpi-card">
                    <div class="kpi-label">📏 Длина хода</div>
                    <div class="kpi-value">{result['length_km']:.3f}</div>
                    <div style="font-size: 0.85rem; color: #8b98a5;">км</div>
                </div>
                """, unsafe_allow_html=True)
            
            with col2:
                st.markdown(f"""
                <div class="kpi-card">
                    <div class="kpi-label">🔢 Станций</div>
                    <div class="kpi-value">{result['n_stations']}</div>
                    <div style="font-size: 0.85rem; color: #8b98a5;">шт</div>
                </div>
                """, unsafe_allow_html=True)
            
            with col3:
                st.markdown(f"""
                <div class="kpi-card">
                    <div class="kpi-label">📐 Невязка fh</div>
                    <div class="kpi-value">{result['misclosure_mm']:.2f}</div>
                    <div style="font-size: 0.85rem; color: #8b98a5;">мм</div>
                </div>
                """, unsafe_allow_html=True)
            
            with col4:
                st.markdown(f"""
                <div class="kpi-card">
                    <div class="kpi-label">✅ Допуск</div>
                    <div class="kpi-value">±{result['allowed_mm']:.2f}</div>
                    <div style="font-size: 0.85rem; color: #8b98a5;">мм</div>
                </div>
                """, unsafe_allow_html=True)
            
            if result["is_valid"]:
                st.markdown('<div class="success-box">✅ Невязка в допуске — ход уравнен</div>', unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="error-box">🚨 {result["error"]}</div>', unsafe_allow_html=True)
            
            st.markdown(f"""
            <div class="glass-card">
                <div style="font-size: 0.9rem; color: #8b98a5; margin-bottom: 0.5rem;">📐 Формула допуска</div>
                <div style="font-size: 1.2rem; font-weight: 600; color: #667eea;">{result['formula_used']}</div>
            </div>
            """, unsafe_allow_html=True)
            
            if stats:
                st.markdown("### 📈 Статистика")
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Мин. превышение", f"{stats.get('min_dh', 0):.4f} м")
                col2.metric("Макс. превышение", f"{stats.get('max_dh', 0):.4f} м")
                col3.metric("Среднее плечо", f"{stats.get('mean_dist', 0):.1f} м")
                col4.metric("Станций в допуске", f"{stats.get('stations_in_tolerance', 0)}/{stats.get('total_stations', 0)}")
    
    with tab_ведомость:
        st.subheader("📋 Ведомость увязки превышений")
        
        if all_dfs:
            combined_df = pd.concat(all_dfs, ignore_index=True)
            st.dataframe(combined_df, width='stretch', hide_index=True)
    
    with tab_profile:
        st.subheader("📈 Профиль хода")
        
        if all_results and all_results[0].get("points"):
            result = all_results[0]
            
            fig = go.Figure()
            
            distances = [p.distance_from_start for p in result["points"]]
            heights = [p.h_adjusted for p in result["points"]]
            point_ids = [p.point_id for p in result["points"]]
            
            fig.add_trace(go.Scatter(
                x=distances, y=heights,
                mode="lines+markers+text",
                text=point_ids,
                textposition="top center",
                name="Профиль",
                line=dict(color="#667eea", width=3),
                marker=dict(size=10, color="#764ba2")
            ))
            
            fig.update_layout(
                title="Продольный профиль нивелирного хода",
                xaxis_title="Расстояние от начала, м",
                yaxis_title="Отметка, м",
                height=500,
                template="plotly_dark",
                showlegend=False,
                hovermode='x unified'
            )
            st.plotly_chart(fig, width='stretch')
            
            if result.get("cumulative_misclosure"):
                st.markdown("### 📊 Накопленная невязка")
                
                cum_data = result["cumulative_misclosure"]
                cum_distances = [c["distance"] for c in cum_data]
                cum_values = [c["cumulative_mm"] for c in cum_data]
                
                fig2 = go.Figure()
                fig2.add_trace(go.Scatter(
                    x=cum_distances, y=cum_values,
                    mode="lines+markers",
                    name="Накопленная невязка",
                    line=dict(color="#f5576c", width=2),
                    marker=dict(size=8)
                ))
                fig2.add_hline(y=0, line_dash="dash", line_color="gray")
                fig2.update_layout(
                    title="Накопленная невязка по станциям",
                    xaxis_title="Расстояние, м",
                    yaxis_title="Накопленная невязка, мм",
                    height=400,
                    template="plotly_dark"
                )
                st.plotly_chart(fig2, width='stretch')
    
    with tab_quality:
        st.subheader("🔍 Контроль качества")
        
        if all_results:
            result = all_results[0]
            quality_issues = result.get("quality_issues", [])
            
            if quality_issues:
                errors = [i for i in quality_issues if i.severity == 'error']
                warnings = [i for i in quality_issues if i.severity == 'warning']
                
                if errors:
                    st.markdown(f'<div class="error-box">⛔ Найдено ошибок: **{len(errors)}**</div>', unsafe_allow_html=True)
                    for issue in errors:
                        st.markdown(f"• **Станция {issue.station_id}** ({issue.point_from} → {issue.point_to}): {issue.message}")
                
                if warnings:
                    st.markdown(f'<div class="warning-box">⚠️ Найдено предупреждений: **{len(warnings)}**</div>', unsafe_allow_html=True)
                    for issue in warnings[:10]:
                        st.markdown(f"• {issue.message}")
                    if len(warnings) > 10:
                        st.caption(f"... и ещё {len(warnings) - 10} предупреждений")
            else:
                st.markdown('<div class="success-box">✅ Контроль качества пройден — все измерения в допуске</div>', unsafe_allow_html=True)
    
    with tab_editor:
        st.subheader("✏️ Редактор точек")
        st.info("Здесь можно будет удалять станции и редактировать отметки (в разработке)")
        
        if all_dfs:
            st.markdown("### Текущие данные")
            st.dataframe(all_dfs[0], width='stretch', hide_index=True)
    
    with tab_export:
        st.subheader("💾 Экспорт результатов")
        
        if all_dfs:
            combined_df = pd.concat(all_dfs, ignore_index=True)
            
            col_a, col_b, col_c = st.columns(3)
            
            csv = combined_df.to_csv(index=False).encode('utf-8-sig')
            col_a.download_button(
                " Скачать CSV",
                data=csv,
                file_name=f"{job_name}_report.csv",
                mime="text/csv",
                width='stretch'
            )
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                combined_df.to_excel(writer, sheet_name='Отчёт', index=False)
            col_b.download_button(
                "📥 Скачать Excel",
                data=buffer.getvalue(),
                file_name=f"{job_name}_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width='stretch'
            )
            
            if all_results[0].get("points"):
                try:
                    pdf_content = PDFExporter.create_report(all_results[0], job_name, level_class)
                    col_c.download_button(
                        " Скачать PDF",
                        data=pdf_content,
                        file_name=f"{job_name}_report.pdf",
                        mime="application/pdf",
                        width='stretch'
                    )
                except Exception as pdf_err:
                    st.warning(f"⚠️ Ошибка генерации PDF: {pdf_err}")

except Exception as e:
    st.error(f"❌ Ошибка обработки: {e}")
    import traceback
    st.code(traceback.format_exc())

st.divider()
st.markdown(f"""
<div style="display: flex; justify-content: space-between; color: #8b98a5; font-size: 0.85rem; padding: 1rem 0;">
    <span>📐 Geogeolevel v9.0</span>
    <span>📅 {datetime.now().strftime('%d.%m.%Y')}</span>
    <span>⚡ Professional Edition</span>
</div>
""", unsafe_allow_html=True)
