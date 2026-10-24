-- IST 210 Lab 2: tables from the Lab 1 Microsoft Learn tutorial + sample data.
-- Run in SSMS against ist210-db-sifat (NOT master). Safe to re-run: it only
-- creates tables that are missing and only seeds empty tables.
--
-- The DDL is the tutorial's, with ON DELETE NO ACTION written out explicitly.
-- (NO ACTION is already the default for a FOREIGN KEY; stating it just makes the
-- referential action visible for the rubric.)

IF OBJECT_ID('dbo.Person', 'U') IS NULL
CREATE TABLE Person
(
    PersonId INT IDENTITY PRIMARY KEY,
    FirstName NVARCHAR (128) NOT NULL,
    MiddleInitial NVARCHAR (10),
    LastName NVARCHAR (128) NOT NULL,
    DateOfBirth DATE NOT NULL
);
GO

IF OBJECT_ID('dbo.Student', 'U') IS NULL
CREATE TABLE Student
(
    StudentId INT IDENTITY PRIMARY KEY,
    PersonId INT FOREIGN KEY REFERENCES Person (PersonId) ON DELETE NO ACTION,
    Email NVARCHAR (256)
);
GO

IF OBJECT_ID('dbo.Course', 'U') IS NULL
CREATE TABLE Course
(
    CourseId INT IDENTITY PRIMARY KEY,
    Name NVARCHAR (50) NOT NULL,
    Teacher NVARCHAR (256) NOT NULL
);
GO

IF OBJECT_ID('dbo.Credit', 'U') IS NULL
CREATE TABLE Credit
(
    StudentId INT FOREIGN KEY REFERENCES Student (StudentId) ON DELETE NO ACTION,
    CourseId INT FOREIGN KEY REFERENCES Course (CourseId) ON DELETE NO ACTION,
    Grade DECIMAL (5, 2) CHECK (Grade <= 100.00),
    Attempt TINYINT,
    CONSTRAINT [UQ_studentgrades]
        UNIQUE CLUSTERED (StudentId, CourseId, Grade, Attempt)
);
GO

-- ---------------------------------------------------------------------------
-- Sample data (the tutorial's CSV files aren't needed; this is self-contained).
-- Eli Novak has no enrollments and "Cloud Computing" has no enrollments, so you
-- can demo a successful parent delete. Everyone else is protected by the FKs.
-- ---------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM Person)
INSERT INTO Person (FirstName, MiddleInitial, LastName, DateOfBirth) VALUES
    ('Noe',    NULL, 'Coleman', '2003-04-12'),
    ('Avery',  'J',  'Brooks',  '2002-09-30'),
    ('Mina',   NULL, 'Haddad',  '2004-01-21'),
    ('Jonas',  'P',  'Weber',   '2001-11-05'),
    ('Sofia',  NULL, 'Alvarez', '2003-07-18'),
    ('Tariq',  'M',  'Rahman',  '2002-03-09'),
    ('Hannah', NULL, 'Lee',     '2004-06-27'),
    ('Eli',    NULL, 'Novak',   '2003-12-02');
GO

IF NOT EXISTS (SELECT 1 FROM Course)
INSERT INTO Course (Name, Teacher) VALUES
    ('Intro to Databases', 'Dominick Pope'),
    ('Data Structures',    'Dominick Pope'),
    ('Network Security',   'Priya Raman'),
    ('Operating Systems',  'Lena Fischer'),
    ('Cyber Law & Ethics', 'Priya Raman'),
    ('Cloud Computing',    'Lena Fischer');
GO

IF NOT EXISTS (SELECT 1 FROM Student)
INSERT INTO Student (PersonId, Email)
SELECT p.PersonId, v.Email
FROM (VALUES
    ('Noe',    'Coleman', 'noe.coleman@example.edu'),
    ('Avery',  'Brooks',  'avery.brooks@example.edu'),
    ('Mina',   'Haddad',  'mina.haddad@example.edu'),
    ('Jonas',  'Weber',   'jonas.weber@example.edu'),
    ('Sofia',  'Alvarez', 'sofia.alvarez@example.edu'),
    ('Tariq',  'Rahman',  'tariq.rahman@example.edu'),
    ('Hannah', 'Lee',     'hannah.lee@example.edu'),
    ('Eli',    'Novak',   'eli.novak@example.edu')
) AS v (FirstName, LastName, Email)
INNER JOIN Person AS p
    ON p.FirstName = v.FirstName AND p.LastName = v.LastName;
GO

IF NOT EXISTS (SELECT 1 FROM Credit)
INSERT INTO Credit (StudentId, CourseId, Grade, Attempt)
SELECT s.StudentId, c.CourseId, v.Grade, v.Attempt
FROM (VALUES
    ('Noe',    'Coleman', 'Intro to Databases', 88.50, 1),
    ('Noe',    'Coleman', 'Data Structures',    72.00, 1),
    ('Noe',    'Coleman', 'Network Security',   91.25, 1),
    ('Avery',  'Brooks',  'Intro to Databases', 79.00, 1),
    ('Avery',  'Brooks',  'Operating Systems',  64.50, 1),
    ('Avery',  'Brooks',  'Operating Systems',  81.00, 2),
    ('Mina',   'Haddad',  'Data Structures',    95.00, 1),
    ('Mina',   'Haddad',  'Cyber Law & Ethics', 89.75, 1),
    ('Jonas',  'Weber',   'Intro to Databases', 58.00, 1),
    ('Jonas',  'Weber',   'Intro to Databases', 76.50, 2),
    ('Jonas',  'Weber',   'Network Security',   70.00, 1),
    ('Sofia',  'Alvarez', 'Operating Systems',  92.00, 1),
    ('Sofia',  'Alvarez', 'Cyber Law & Ethics', 85.50, 1),
    ('Tariq',  'Rahman',  'Network Security',   77.25, 1),
    ('Tariq',  'Rahman',  'Data Structures',    83.00, 1)
) AS v (FirstName, LastName, CourseName, Grade, Attempt)
INNER JOIN Person AS p  ON p.FirstName = v.FirstName AND p.LastName = v.LastName
INNER JOIN Student AS s ON s.PersonId = p.PersonId
INNER JOIN Course AS c  ON c.Name = v.CourseName;
GO

-- Quick check
SELECT (SELECT COUNT(*) FROM Person)  AS Persons,
       (SELECT COUNT(*) FROM Student) AS Students,
       (SELECT COUNT(*) FROM Course)  AS Courses,
       (SELECT COUNT(*) FROM Credit)  AS Credits;
