"""Small, source-gated math fallback. No model, network, or platform answers.

These templates run only for future self-tests. Missing verified source text
means no question, rather than treating OCR or a familiar formula as evidence.
"""
import re
from .models import QuizQuestion


def make(question, options, correct, explanation, source):
    return QuizQuestion(question=question,options=dict(zip('ABCD',options)),correct_key=correct,
                        explanation=explanation,source_ids=[source['source_id']])


def retest_family(question):
    """Conservative skill identity, never an answer to the classroom question."""
    if '无穷小' in question and '等价' in question:return 'equivalent_infinitesimal'
    if '夹逼' in question or '≤f(x)≤' in question and 'x²' in question:return 'squeeze'
    if 'sin' in question:
        return 'sine_zero' if re.search(r'x\s*(?:→|->|趋于)\s*0',question) else None
    if '重要极限' in question:return None
    if any(word in question for word in ['没有定义','无定义','去心邻域','另规定']):return 'point_limit'
    if any(word in question for word in ['量词','ε','δ','定义']):return 'function_definition'
    compact=re.sub(r'\s+','',question)
    match=re.search(r'lim.*?x(?:→|->)(\d+).*?\(x(?:²|\^2)[−-](\d+)\)/\(x[−-](\d+)\)',compact)
    if match and int(match[1])==int(match[3]) and int(match[2])==int(match[3])**2:return 'removable_rational'
    return None


def function_limit_diagnostic(sources):
    definition=next((s for s in sources if '0<|x−x0|<δ' in s['quote'] and '任意' in s['quote']),None)
    point=next((s for s in sources if '有无定义无关' in s['quote']),None)
    example=next((s for s in sources if '(x²−1)/(x−1)=2' in s['quote']),None)
    if not all([definition,point,example]): return None
    return [
        make('要用定义说明 lim(x→x₀) f(x)=A，哪一项量词与条件完整？',
            ['任给 ε>0，都存在 δ>0；对每个满足 0<|x−x₀|<δ 的 x，都有 |f(x)−A|<ε。',
             '只要选一个 ε>0，使某个 x 满足不等式即可。',
             '只需 f(x₀)=A，不需要考察附近的函数值。',
             '允许对邻域中的每个 x 临时改变同一次 ε 所选的 δ。'],'A',
             '定义先任给 ε，再选 δ，并要求去心邻域中所有符合条件的 x 都满足误差要求。',definition),
        make('某函数在 x₀ 处没有定义。仅凭这一事实，能否判定 x→x₀ 时的极限不存在？',
            ['能，极限存在必须先在 x₀ 有定义。','不能；极限考察去心邻域，点上的定义本身不能决定附近的趋向。',
             '能，但只对多项式成立。','不能，因为任何函数在任何点都有极限。'],'B',
             '教材明确说明极限与该点是否有定义无关；仍要检查去心邻域内的变化趋势。',point),
        make('独立计算：lim(x→2) (x²−4)/(x−2) 等于多少？',
            ['不存在，因为分母在 2 处为零。','2','4','0'],'C',
             '当 x≠2 时原式等于 x+2，所以趋于 4。与教材例4相同，去心邻域中的化简不要求原函数在该点有定义。',example),
    ]


def retest(sources, original, seed):
    example=next((s for s in sources if '(x²−1)/(x−1)=2' in s['quote']),None)
    sine=next((s for s in sources if 'sin' in s['quote'] and 'x→0' in s['quote']),None)
    squeeze=next((s for s in sources if '夹逼' in s['quote'] and ('条件' in s['quote'] or '定理' in s['quote'])),None)
    definition=next((s for s in sources if '0<|x−x0|<δ' in s['quote'] and '任意' in s['quote']),None)
    point=next((s for s in sources if '有无定义无关' in s['quote']),None)
    k=3+int(seed[:4],16)%7
    if retest_family(original)=='equivalent_infinitesimal':
        common=next((s for s in sources if all(term in re.sub(r'\s+','',s['quote']) for term in ['x→0','sinx~x','tanx~x','ln(1+x)~x','1−cosx~x²/2'])),None)
        if not common:return None
        choices=[f'sin({k}x)、tan({k}x)、ln(1+{k}x)',f'sin({k}x)、1−cos({k}x)、ln(1+{k}x)',
            f'1−cos({k}x)、tan({k}x)、{k}x²',f'ln(1+{k}x)、{k}x²、1−cos({k}x)']
        rotation=k%4;choices=choices[rotation:]+choices[:rotation]
        return make(f'后续独立复测：当 x→0 时，以下哪组三个无穷小量都与 {k}x 等价？',choices,
            'ABCD'[(-rotation)%4],f'令 t={k}x，则 t→0。教材给出 sin t~t、tan t~t、ln(1+t)~t，而 1−cos t~t²/2，不能把它与t视为等价。',common)
    if '夹逼' in original:
        if not squeeze:return None
        return make(f'后续复测：在 0<|x|<1 内有 −{k}x²≤f(x)≤{k}x²。x→0 时 f(x) 的极限是什么？',
            ['无法判断','0',str(k),str(-k)],'B','上下界在 x→0 时都趋于 0，且在同一去心邻域保持有序关系，因此可用夹逼准则。',squeeze)
    if 'sin' in original:
        if not sine or not re.search(r'x\s*(?:→|->|趋于)\s*0',original):return None
        return make(f'后续复测（弧度制）：lim(x→0) sin({k}x)/x 等于多少？',
            ['0','1',str(k),f'1/{k}'],'C',f'令 t={k}x，则 t→0，原式为 {k}·sin(t)/t，极限为 {k}。先检查趋向和弧度制条件。',sine)
    if '重要极限' in original:return None  # The first and second important limits are different skills.
    if any(word in original for word in ['没有定义','无定义','去心邻域']):
        if not point:return None
        c=k+2;point_value=100+7*k
        return make(f'后续复测：当 0<|x−{k}|<1 时 f(x)={c}，另规定 f({k})={point_value}。x→{k} 时 f(x) 的极限是什么？',
            [str(point_value),'0',str(c),'不存在'],'C',f'去心邻域内函数值恒为 {c}；该点的函数值不决定极限。改变点值并不改变附近的趋向。',point)
    if any(word in original for word in ['量词','ε','δ','定义']):
        if not definition:return None
        return make('后续复测：按函数极限的定义，任给 ε>0 后所选的 δ 应满足什么要求？',
            ['可以随邻域中的每一个 x 临时改变。','可以依赖 ε，但要一次控制该去心邻域中所有满足条件的 x。',
             '只需要控制一个 x。','只要保证点上的函数值等于极限值。'],'B',
             '定义的顺序是任给 ε、存在 δ、对所有满足去心邻域条件的 x 成立，不能逐点重选 δ。',definition)
    compact=re.sub(r'\s+','',original)
    rational=re.search(r'lim.*?x(?:→|->)(\d+).*?\(x(?:²|\^2)[−-](\d+)\)/\(x[−-](\d+)\)',compact)
    if example and rational and int(rational[1])==int(rational[3]) and int(rational[2])==int(rational[3])**2:
        return make(f'后续复测：lim(x→{k}) (x²−{k*k})/(x−{k}) 等于多少？',
            ['不存在',str(k),'0',str(2*k)],'D',f'在 x≠{k} 的去心邻域中，原式等于 x+{k}，故极限为 {2*k}；点处是否有定义不影响这个结论。',example)
    return None
