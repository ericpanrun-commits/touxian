CREATE TABLE course (
 id TEXT PRIMARY KEY,
 name TEXT NOT NULL CHECK(length(trim(name)) > 0),
 normalized_name TEXT NOT NULL UNIQUE
);
CREATE TABLE course_alias (
 normalized_alias TEXT PRIMARY KEY,
 course_id TEXT NOT NULL REFERENCES course(id)
);
CREATE INDEX course_alias_course ON course_alias(course_id);
ALTER TABLE event ADD COLUMN course_id TEXT REFERENCES course(id);
CREATE INDEX event_course ON event(course_id);

INSERT INTO course(id,name,normalized_name)
VALUES ('course-math-ii','高等数学 II','高等数学ii');
INSERT INTO course_alias(normalized_alias,course_id) VALUES
 ('高等数学ii','course-math-ii'),
 ('高等数学','course-math-ii'),
 ('高数','course-math-ii');

