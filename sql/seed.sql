-- Seed data. Companies / rounds / JDs are SAMPLE data for development only.
-- Replace with verified, real hiring data before showing it to students.
INSERT INTO skills (name, category, aliases, est_hours) VALUES
 ('Python','language','{py,python3}',20), ('Java','language','{}',25),
 ('C++','language','{cpp,"c plus plus"}',25), ('C','language','{}',20),
 ('JavaScript','language','{js,ecmascript}',20), ('TypeScript','language','{ts}',12),
 ('SQL','database','{}',15), ('HTML','language','{html5}',6), ('CSS','language','{css3}',8),
 ('React','framework','{reactjs,"react.js"}',20), ('Node.js','framework','{node,nodejs}',15),
 ('Flask','framework','{}',8), ('FastAPI','framework','{}',8), ('Django','framework','{}',15),
 ('Spring Boot','framework','{springboot,spring}',20),
 ('PostgreSQL','database','{postgres,psql}',12), ('MySQL','database','{}',10),
 ('MongoDB','database','{mongo}',10), ('Git','tool','{github,gitlab}',5),
 ('Docker','tool','{containers}',8), ('Kubernetes','tool','{k8s}',15),
 ('AWS','tool','{"amazon web services"}',20), ('Linux','tool','{unix,bash}',10),
 ('REST APIs','framework','{rest,restful,"rest api"}',8),
 ('Data Structures','cs_fundamental','{dsa,"data structure"}',40),
 ('Algorithms','cs_fundamental','{algorithm}',40),
 ('Operating Systems','cs_fundamental','{os,"operating system"}',30),
 ('DBMS','cs_fundamental','{rdbms,"database management"}',25),
 ('Computer Networks','cs_fundamental','{networking,"computer network"}',20),
 ('OOP','cs_fundamental','{oops,"object oriented","object-oriented"}',12),
 ('System Design','cs_fundamental','{"low level design","high level design"}',30),
 ('Machine Learning','ai_ml','{ml}',40), ('NLP','ai_ml','{"natural language processing",spacy}',25),
 ('Pandas','ai_ml','{}',10), ('NumPy','ai_ml','{numpy}',8),
 ('Problem Solving','soft','{"problem-solving"}',20), ('Communication','soft','{"communication skills"}',6),
 ('Agile','tool','{scrum}',4), ('CI/CD','tool','{cicd,"github actions",jenkins}',8);

INSERT INTO companies (name, industry) VALUES
 ('Acme Technologies (sample)','Product'),
 ('Globex Systems (sample)','Services'),
 ('Initech Software (sample)','Product');

INSERT INTO job_descriptions (company_id, role_title, description, location) VALUES
 (1,'Backend Developer Intern','Build REST services in Python with SQL databases. Git, Docker and Linux are a plus.','Remote'),
 (2,'Software Engineer','Strong Java, data structures and algorithms. OOP, SQL, Spring Boot and operating systems fundamentals.','Hybrid'),
 (3,'Full Stack Developer','JavaScript, React and Node.js with SQL, HTML, CSS, TypeScript, Git and REST APIs.','Onsite');

INSERT INTO jd_skills (jd_id, skill_id, importance, required)
SELECT j.id, s.id, v.imp, v.req FROM (VALUES
 (1,'Python',5,true),(1,'SQL',5,true),(1,'REST APIs',4,true),(1,'PostgreSQL',4,true),
 (1,'Data Structures',4,true),(1,'Git',3,true),(1,'Docker',2,false),(1,'Linux',2,false),
 (2,'Java',5,true),(2,'Data Structures',5,true),(2,'Algorithms',5,true),(2,'OOP',4,true),
 (2,'SQL',3,true),(2,'Spring Boot',3,false),(2,'Operating Systems',3,true),(2,'System Design',2,false),
 (3,'JavaScript',5,true),(3,'React',5,true),(3,'Node.js',4,true),(3,'SQL',3,true),
 (3,'HTML',3,true),(3,'CSS',3,true),(3,'Git',3,true),(3,'REST APIs',3,true),(3,'TypeScript',2,false)
) AS v(jd, skill, imp, req)
JOIN job_descriptions j ON j.id = v.jd JOIN skills s ON s.name = v.skill;

INSERT INTO hiring_rounds (company_id, round_no, name, round_type, description, duration_min, tips) VALUES
 (1,1,'Online Assessment','online_test','Aptitude plus basic coding questions.',90,'Practice timed problems; read constraints carefully.'),
 (1,2,'Technical Interview','technical','Projects, Python, SQL and data structures.',45,'Be ready to explain every project line on your resume.'),
 (1,3,'HR Interview','hr','Motivation, teamwork and expectations.',30,'Prepare 3 stories using situation-action-result.'),
 (2,1,'Online Coding Test','coding','Two DSA problems and MCQs on CS fundamentals.',100,'Revise arrays, strings, trees, and OS/DBMS basics.'),
 (2,2,'Technical Interview 1','technical','Java, OOP and problem solving on a whiteboard.',45,'Talk through your approach before coding.'),
 (2,3,'Technical Interview 2','system_design','Low level design and project deep-dive.',45,'Know the trade-offs behind your project choices.'),
 (2,4,'HR Interview','hr','Culture fit and offer discussion.',30,'Research the company and ask good questions.'),
 (3,1,'Take-home Assignment','coding','Small full stack feature with tests.',240,'Clean commits and a clear README matter.'),
 (3,2,'Code Review Interview','technical','Walk through your assignment and extend it live.',60,'Be ready to defend design decisions.'),
 (3,3,'HR Interview','hr','Team fit and expectations.',30,'Be honest about what you do not know yet.');

INSERT INTO resources (skill_id, title, url, kind, difficulty)
SELECT s.id, v.title, v.url, v.kind, v.diff FROM (VALUES
 ('Python','Official Python Tutorial','https://docs.python.org/3/tutorial/','docs',1),
 ('PostgreSQL','PostgreSQL Tutorial (official docs)','https://www.postgresql.org/docs/current/tutorial.html','docs',1),
 ('SQL','PostgreSQL Tutorial (official docs)','https://www.postgresql.org/docs/current/tutorial.html','docs',1),
 ('DBMS','PostgreSQL Concurrency Control (MVCC)','https://www.postgresql.org/docs/current/mvcc.html','docs',3),
 ('Git','Pro Git Book','https://git-scm.com/book/en/v2','book',1),
 ('Docker','Docker Get Started','https://docs.docker.com/get-started/','docs',1),
 ('Kubernetes','Kubernetes Tutorials','https://kubernetes.io/docs/tutorials/','docs',2),
 ('React','React Learn','https://react.dev/learn','docs',1),
 ('JavaScript','MDN JavaScript Guide','https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide','docs',1),
 ('HTML','MDN Learn Web Development','https://developer.mozilla.org/en-US/docs/Learn','docs',1),
 ('CSS','MDN Learn Web Development','https://developer.mozilla.org/en-US/docs/Learn','docs',1),
 ('TypeScript','TypeScript Handbook','https://www.typescriptlang.org/docs/handbook/intro.html','docs',2),
 ('Node.js','Node.js Learn','https://nodejs.org/en/learn','docs',1),
 ('FastAPI','FastAPI Tutorial','https://fastapi.tiangolo.com/tutorial/','docs',1),
 ('REST APIs','FastAPI Tutorial','https://fastapi.tiangolo.com/tutorial/','docs',1),
 ('Java','The Java Tutorials','https://docs.oracle.com/javase/tutorial/','docs',1),
 ('Operating Systems','Operating Systems: Three Easy Pieces','https://pages.cs.wisc.edu/~remzi/OSTEP/','book',2),
 ('Data Structures','LeetCode Problem Set','https://leetcode.com/problemset/','practice',2),
 ('Algorithms','LeetCode Problem Set','https://leetcode.com/problemset/','practice',2),
 ('Problem Solving','LeetCode Problem Set','https://leetcode.com/problemset/','practice',2)
) AS v(skill, title, url, kind, diff) JOIN skills s ON s.name = v.skill;
