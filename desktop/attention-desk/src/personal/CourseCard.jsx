import { ChevronRight } from 'lucide-react';
import { courseTheme } from './courseVisuals';

export function CourseArtwork({ course, className = '' }) {
  const theme = courseTheme(course);
  return <span aria-hidden="true" className={`course-artwork ${className}`} style={theme.style} data-course-theme={theme.key}/>;
}

export default function CourseCard({ course, detail, onOpen }) {
  return <button type="button" className="course-card" onClick={onOpen}>
    <CourseArtwork course={course}/>
    <span className="course-card-copy"><strong>{course.name || course.title}</strong><span>{detail || courseTheme(course).label}<ChevronRight size={14}/></span></span>
  </button>;
}
