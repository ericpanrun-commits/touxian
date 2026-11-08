import atlas from '../assets/course-atlas.webp';

// Subject rules, never record identities. A future course can specify visualTheme
// without a schema change; otherwise its name resolves to a subject or fallback.
export const COURSE_THEMES = {
  math: { label: '数学', cell: [0, 0], color: '#174a70' },
  engineering: { label: '工程制图', cell: [1, 0], color: '#225998' },
  computing: { label: '计算机与 AI', cell: [2, 0], color: '#202c40' },
  english: { label: '语言学习', cell: [0, 1], color: '#59516c' },
  vehicle: { label: '车辆工程', cell: [1, 1], color: '#243b4e' },
  history: { label: '历史', cell: [2, 1], color: '#87543e' },
  sport: { label: '体育', cell: [0, 2], color: '#8a4938' },
  career: { label: '生涯规划', cell: [1, 2], color: '#48635d' },
  military: { label: '军事理论', cell: [2, 2], color: '#4c5644' },
  policy: { label: '形势与政策', cell: [0, 3], color: '#3c5267' },
  study: { label: '自主学习', cell: [1, 3], color: '#48566a' },
  general: { label: '课程', cell: [2, 3], color: '#435e68' },
};
const rules = [
  ['math', /数学|微积分|代数|概率|统计|calculus|mathematics/i],
  ['engineering', /图学|制图|CAD|工程绘图/i],
  ['computing', /人工智能|计算机|程序|算法|编程|computer|coding/i],
  ['english', /英语|英文|IELTS|CET|English/i],
  ['vehicle', /车辆|汽车|底盘|automotive/i],
  ['history', /历史|近现代史|史纲/i],
  ['sport', /体育|田径|运动|physical education/i],
  ['career', /职业|生涯|就业/i],
  ['military', /军事|国防/i],
  ['policy', /形势|政策|政治|思想/i],
  ['study', /晚修|晚自习|自习|自主学习/i],
];
export function courseTheme(course = '') {
  const name = typeof course === 'string' ? course : course.name || course.title || '';
  const explicit = typeof course === 'object' && course.visualTheme;
  const key = COURSE_THEMES[explicit] ? explicit : rules.find(([, pattern]) => pattern.test(name))?.[0] || 'general';
  const theme = COURSE_THEMES[key];
  return { ...theme, key, style: { '--course-color': theme.color, '--course-art': `url("${atlas}")`, '--course-position': `${theme.cell[0] * 50}% ${theme.cell[1] * 100 / 3}%` } };
}
