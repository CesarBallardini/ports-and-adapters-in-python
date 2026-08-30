Feature: A guardian sees the wards currently in their care
  UC-28. A guardian is shown the students they are responsible for, and only while they are
  responsible for them.

  The rule this feature exists to demonstrate is that guardianship ends without anyone acting.
  Nothing is written on the ward's birthday, no scheduled job runs, and no record changes: the
  answer is computed from the ward's age against the age of majority every time it is asked.

  Background:
    Given the age of majority is 18
    And Mary is a guardian
    And Ada is a student born on 2011-05-01
    And Mary is registered as Ada's guardian

  Scenario: A guardian sees a ward who is still a minor
    Given today is 2026-08-30
    When Mary lists her wards
    Then the list is Ada

  Scenario: A ward leaves the list on their eighteenth birthday
    Given today is 2029-05-01
    When Mary lists her wards
    Then the list is empty
    And the guardianship between Mary and Ada is still stored

  Scenario: The day before that birthday, the ward is still in care
    Given today is 2029-04-30
    When Mary lists her wards
    Then the list is Ada

  Scenario: Raising the age of majority puts a ward back in care
    Given today is 2029-05-01
    And the age of majority is 21
    When Mary lists her wards
    Then the list is Ada

  Scenario: A ward named by two guardianship records is listed once
    Given today is 2026-08-30
    And Mary is registered as Ada's guardian a second time
    When Mary lists her wards
    Then the list is Ada
